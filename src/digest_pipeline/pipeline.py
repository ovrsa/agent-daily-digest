"""One daily run: collect, normalize and gate, research, select, render, publish, audit, comment.

The run follows the Design Doc's Failure policy. Each stage runs inside
`RunRecorder.stage`, so its status, time and error land in the run metrics,
and the terminal status follows from which stages failed:

- a failure up to and including rendering publishes nothing and leaves the
  processing state as it was, so the same articles are tried again next run
- nothing collected, nothing past the gates, or nothing adopted ends normally
  without a digest; when the Selector decided anything, the processing state
  is still committed, so the excluded articles are not researched again
- a failed publish undoes the files it wrote and skips the audit
- a failed Judge or comment leaves the digest published; the run is
  `partially_failed`, and a report that could not be posted is kept locally

The Judge's report is posted on the digest commit and names the run id, which
is also the name of the run's metrics file, so the three can be matched.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from digest_collect import CollectionReport
from digest_contracts import (
    CollectedItem,
    ErrorKind,
    ErrorRecord,
    EvidencePacket,
    NormalizedArticle,
    ProcessedState,
    ResearchBudget,
    RunStatus,
    SelectorOutput,
    SourceKind,
    StageName,
)
from digest_judge import PROMPT_VERSION as JUDGE_PROMPT_VERSION
from digest_judge import Judge, JudgeResult, finding_metrics, render_report
from digest_llm import StructuredRequest
from digest_normalize import (
    BodyFetcher,
    NormalizationResult,
    ProcessedIndex,
    load_state,
    normalize_items,
    record_article,
    save_state,
)
from digest_observe import (
    Clock,
    LLMResponse,
    MetricsLeakError,
    MetricsStore,
    PricingTable,
    RunRecorder,
    StageFailed,
    article_metrics,
    classify_exception,
    safe_detail,
    utc_now,
)
from digest_render import README_FILENAME, digest_filename, render_digest, write_digest
from digest_research import Researcher, ResearchInput, ResearchResult
from digest_select import Selector

from .config import Models
from .publisher import Publisher

Invoker = Callable[[StructuredRequest], LLMResponse]
Collector = Callable[[frozenset[str]], CollectionReport]


@dataclass(frozen=True)
class Paths:
    digests_dir: Path
    state_path: Path
    judge_log_dir: Path
    """Where a Judge report goes when it could not be posted."""


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: RunStatus
    commit: str | None = None
    """The commit that carries the digest, or the processing state alone when nothing was adopted."""
    digest_path: Path | None = None
    """Set when a digest was published."""
    error: ErrorRecord | None = None
    """Why the run stopped, when an exception ended it, or why its metrics could not be written."""
    metrics_error: ErrorRecord | None = None
    """Set when the metrics record failed while another failure was already ending the run."""

    @property
    def published(self) -> bool:
        return self.digest_path is not None and self.commit is not None


@dataclass
class Pipeline:
    collect: Collector
    """Collects every enabled source, given the canonical URLs already in the processing state."""
    fetch: BodyFetcher
    invoke: Invoker
    pricing: PricingTable
    models: Models
    publisher: Publisher
    paths: Paths
    budget: ResearchBudget = field(default_factory=ResearchBudget)
    store: MetricsStore | None = None
    clock: Clock = utc_now
    max_articles: int | None = None
    """At most this many articles past the gates are researched in one run.

    定点観測 goes first, then the newest. The rest are neither researched nor
    recorded in the processing state, so a later run takes them up. `None`
    researches every article, which on a first run is the whole window.
    """

    def run(self, digest_date: date, *, run_id: str | None = None) -> RunResult:
        """Run once. A failure is a `RunResult` with its status; only an interrupt raises."""
        recorder: RunRecorder

        def sink(metrics) -> None:
            if self.store is not None:
                self.store.write(metrics, sensitive=recorder.sensitive_texts)

        recorder = RunRecorder(run_id, clock=self.clock, sink=sink)
        run = _Run(self, recorder, digest_date)
        try:
            with recorder:
                run.execute()
        except Exception as exc:
            status = recorder.result.status if recorder.result is not None else RunStatus.FAILED
            return RunResult(recorder.run_id, status, run.commit, run.digest_path, _error_of(exc), _sink_error(recorder))
        return RunResult(recorder.run_id, recorder.result.status, run.commit, run.digest_path)


@dataclass
class _Run:
    """The state of one run as its stages fill it in."""

    pipeline: Pipeline
    recorder: RunRecorder
    digest_date: date
    commit: str | None = None
    digest_path: Path | None = None

    def execute(self) -> None:
        p, recorder = self.pipeline, self.recorder
        with recorder.stage(StageName.COLLECT):
            p.publisher.sync()
            state = load_state(p.paths.state_path)
            report = p.collect(frozenset(record.canonical_url for record in state.records))
            recorder.record_sources(report.metrics)
            if report.results and len(report.failed_source_ids) == len(report.results):
                first = next(result.failure for result in report.results if result.failure is not None)
                raise StageFailed(first.kind, f"all {len(report.results)} sources failed")
        items = report.items
        if not items:
            return

        with recorder.stage(StageName.NORMALIZE):
            index = ProcessedIndex.from_state(state)
            results = normalize_items(items, fetch=p.fetch, index=index)
        with recorder.stage(StageName.GATE):
            passed = _within(p.max_articles, [result for result in results if result.passed])
            self._record_articles(items, results)
            recorder.mark_sensitive(*(r.article.body_text for r in results if r.article is not None))
        if not passed:
            return

        with recorder.stage(StageName.RESEARCH):
            researcher = Researcher(
                invoke=p.invoke,
                fetch=p.fetch,
                pricing=p.pricing,
                model=p.models.research,
                budget=p.budget,
                processed=index,
                record=recorder.add_llm_call,
            )
            research = researcher.run([_research_input(result) for result in passed])

        with recorder.stage(StageName.SELECT):
            selection = Selector(
                invoke=p.invoke,
                pricing=p.pricing,
                model=p.models.selector,
                record=recorder.add_llm_call,
                library=research.library,
            ).select(research.packets)
            if selection.output is None:
                error = selection.error or ErrorRecord(kind=ErrorKind.UNEXPECTED)
                raise StageFailed(error.kind, error.detail)
            output = selection.output
            self._record_articles(items, results, output)

        articles = {r.article.article_id: r.article for r in passed if r.article is not None}
        with recorder.stage(StageName.RENDER):
            adopted = render_digest(output, articles, self.digest_date) is not None

        with recorder.stage(StageName.PUBLISH):
            decided = _decided_state(state, passed, output, self.pipeline.clock())
            self._publish(output, articles, decided, adopted)
        if not adopted:
            return

        judged = self._audit(output, research)
        self._comment(judged, research.packets)

    # -- stages that need more than a few lines --------------------------------

    def _publish(self, output: SelectorOutput, articles: dict[str, NormalizedArticle], decided: ProcessedState, adopted: bool) -> None:
        paths = self.pipeline.paths
        written = _Snapshot.of(paths.digests_dir / digest_filename(self.digest_date), paths.digests_dir / README_FILENAME, paths.state_path)
        try:
            files: list[Path] = []
            if adopted:
                digest = write_digest(output, articles, self.digest_date, paths.digests_dir)
                files += [digest, paths.digests_dir / README_FILENAME]
            save_state(paths.state_path, decided)
            files.append(paths.state_path)
            kind = "digest" if adopted else "state"
            commit = self.pipeline.publisher.publish(files, f"{kind}: {self.digest_date.isoformat()}")
        except BaseException:
            written.restore()
            raise
        self.commit = commit
        if adopted:
            self.digest_path = paths.digests_dir / digest_filename(self.digest_date)
            self.recorder.set_published(len(output.must_read), len(output.worth_knowing))

    def _audit(self, output: SelectorOutput, research: ResearchResult) -> JudgeResult:
        """Audit the published digest. Any failure, a bug included, is recorded and returned, not raised."""
        p = self.pipeline
        result: JudgeResult | None = None
        try:
            with self.recorder.stage(StageName.JUDGE):
                result = Judge(
                    invoke=p.invoke,
                    pricing=p.pricing,
                    model=p.models.judge,
                    record=self.recorder.add_llm_call,
                    library=research.library,
                ).audit(output, research.packets)
                if result.error is not None:
                    raise StageFailed(result.error.kind, result.error.detail)
                self.recorder.record_findings(finding_metrics(result.report))
        except Exception as exc:
            if result is None:
                result = JudgeResult(
                    report=None,
                    targets=(),
                    model=p.models.judge,
                    prompt_version=JUDGE_PROMPT_VERSION,
                    error=classify_exception(exc),
                )
        return result

    def _comment(self, judged: JudgeResult, packets: Sequence[EvidencePacket]) -> None:
        """Post the report on the digest commit, or keep it in the local log when that fails."""
        if self.commit is None:
            raise RuntimeError("a comment follows a published digest")
        body = comment_body(self.recorder.run_id, self.digest_date, render_report(judged, packets))
        try:
            with self.recorder.stage(StageName.COMMENT):
                self.pipeline.publisher.comment(self.commit, body)
        except Exception:
            log_dir = self.pipeline.paths.judge_log_dir
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / f"{self.recorder.run_id}.md").write_text(f"commit: {self.commit}\n\n{body}", encoding="utf-8")

    def _record_articles(
        self,
        items: Sequence[CollectedItem],
        results: Sequence[NormalizationResult],
        selection: SelectorOutput | None = None,
    ) -> None:
        for item, result in zip(items, results):
            extracted = len(result.extracted.body_text) if result.extracted is not None and result.article is None else None
            self.recorder.record_article(
                article_metrics(item, result.outcome, result.article, selection=selection, extracted_chars=extracted)
            )


def _sink_error(recorder: RunRecorder) -> ErrorRecord | None:
    return _error_of(recorder.sink_error) if recorder.sink_error is not None else None


def _error_of(exc: BaseException) -> ErrorRecord:
    """Classify `exc`. A refused metrics record names where the copy or secret was, never the text."""
    if isinstance(exc, MetricsLeakError):
        places = ", ".join(f"{leak.location} ({leak.kind})" for leak in exc.leaks)
        return ErrorRecord(kind=ErrorKind.VALIDATION, detail=safe_detail(f"metrics refused: {places}"))
    return classify_exception(exc)


def comment_body(run_id: str, digest_date: date, report: str) -> str:
    """The Judge report with the run it belongs to, for the digest commit."""
    return f"run: `{run_id}` / digest: {digest_date.isoformat()}\n\n{report}"


def _within(cap: int | None, passed: list[NormalizationResult]) -> list[NormalizationResult]:
    """The articles a run takes up under `cap`: 定点観測 first, then the newest, in collection order otherwise."""
    if cap is None or len(passed) <= cap:
        return passed
    ranked = sorted(
        passed,
        key=lambda r: (r.article.source_kind is not SourceKind.FIXED_WATCH, -r.article.published_at.timestamp()),
    )
    chosen = {id(r) for r in ranked[:cap]}
    return [r for r in passed if id(r) in chosen]


def _research_input(result: NormalizationResult) -> ResearchInput:
    assert result.article is not None
    links = result.extracted.links if result.extracted is not None else ()
    return ResearchInput(article=result.article, links=links)


def _decided_state(state: ProcessedState, passed: Sequence[NormalizationResult], output: SelectorOutput, now: datetime) -> ProcessedState:
    """The processing state with every article the Selector decided and its decision."""
    for result in passed:
        if result.article is None:
            continue
        decision, _tier = output.decision_of(result.article.article_id)
        state = record_article(state, result.article, decision, seen_at=now)
    return state


@dataclass(frozen=True)
class _Snapshot:
    """The bytes of some files before a publish wrote them, to put back if it fails."""

    before: tuple[tuple[Path, bytes | None], ...]

    @classmethod
    def of(cls, *paths: Path) -> _Snapshot:
        return cls(tuple((path, path.read_bytes() if path.exists() else None) for path in paths))

    def restore(self) -> None:
        for path, content in self.before:
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
