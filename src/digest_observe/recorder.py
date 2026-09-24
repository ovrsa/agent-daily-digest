"""Collects one run's metrics as it happens and hands them over however it ends.

`RunRecorder` is the collection side of the observability layer. It holds no
aggregation and no presentation; `summary.py` and `report.py` read the
`RunMetrics` it produces.

The recorder is a context manager so a run cannot end without a record: when
the body raises, including `KeyboardInterrupt`, the stage that was running is
failed, the stages that never ran are skipped, the run gets a terminal status,
and the sink receives the result before the exception continues.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from types import TracebackType

from pydantic import ValidationError

from digest_contracts import (
    ArticleMetrics,
    CollectedItem,
    ErrorKind,
    ErrorRecord,
    FindingMetrics,
    GateOutcome,
    LLMCallMetrics,
    NormalizedArticle,
    RunMetrics,
    RunStatus,
    SelectorOutput,
    SourceMetrics,
    StageMetrics,
    StageName,
    StageStatus,
)

from .clock import Clock, utc_now
from .safety import describe_exception, safe_detail

TOLERATED_STAGE_FAILURES = frozenset({StageName.JUDGE, StageName.COMMENT})
"""Stages whose failure leaves the digest published (Design Doc, Failure policy)."""

Sink = Callable[[RunMetrics], None]


class StageFailed(Exception):
    """A classified stage failure. Raise it inside `RunRecorder.stage` to name the kind."""

    def __init__(self, kind: ErrorKind, detail: str | None = None) -> None:
        super().__init__(detail or kind.value)
        self.kind = kind
        self.detail = detail


class RunAborted(Exception):
    """Raised by the flow to stop the run on purpose, e.g. when a budget is exhausted."""


def new_run_id(now: datetime | None = None) -> str:
    """`run-<UTC timestamp>-<6 hex>`: sortable by time and unique across same-second runs."""
    moment = (now or utc_now()).astimezone(timezone.utc)
    return f"run-{moment:%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"


def derive_status(stages: Iterable[StageMetrics]) -> RunStatus:
    """The terminal status a run earns from its stages."""
    failed = {stage.stage for stage in stages if stage.status is StageStatus.FAILED}
    if not failed:
        return RunStatus.SUCCEEDED
    if failed <= TOLERATED_STAGE_FAILURES:
        return RunStatus.PARTIALLY_FAILED
    return RunStatus.FAILED


def classify_exception(exc: BaseException) -> ErrorRecord:
    """The `ErrorRecord` a stage keeps for an exception, without quoting its message."""
    if isinstance(exc, StageFailed):
        return ErrorRecord(kind=exc.kind, detail=safe_detail(exc.detail))
    if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError, RunAborted)):
        return ErrorRecord(kind=ErrorKind.CANCELLED, detail=describe_exception(exc))
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return ErrorRecord(kind=ErrorKind.TIMEOUT, detail=describe_exception(exc))
    if isinstance(exc, ValidationError):
        return ErrorRecord(kind=ErrorKind.VALIDATION, detail=f"{exc.error_count()} validation issue(s)")
    return ErrorRecord(kind=ErrorKind.UNEXPECTED, detail=describe_exception(exc))


def article_metrics(
    item: CollectedItem,
    outcome: GateOutcome,
    article: NormalizedArticle | None = None,
    *,
    selection: SelectorOutput | None = None,
    extracted_chars: int | None = None,
) -> ArticleMetrics:
    """One article's record: gate result, body size, and the Selector's decision if any.

    `extracted_chars` is the length of what extraction produced when the article
    did not pass, so a page that yielded too little text shows how little.
    """
    scores = decision = tier = reason = None
    if selection is not None and outcome.passed:
        try:
            decision, tier = selection.decision_of(item.article_id)
        except KeyError:
            decision = tier = None
        else:
            evaluated = next(
                a for a in (*selection.included, *selection.excluded) if a.article_id == item.article_id
            )
            scores, reason = evaluated.scores, evaluated.decision_reason
    return ArticleMetrics(
        article_id=item.article_id,
        source_id=item.source_id,
        canonical_url=article.canonical_url if article is not None else None,
        body_source=article.body_source if article is not None else None,
        char_count=article.char_count if article is not None else extracted_chars,
        gate=outcome,
        scores=scores,
        decision=decision,
        tier=tier,
        decision_reason=reason,
    )


class RunRecorder:
    """Collects the metrics of one run. Use it as a context manager."""

    def __init__(self, run_id: str | None = None, *, clock: Clock = utc_now, sink: Sink | None = None) -> None:
        self._clock = clock
        self._sink = sink
        self._started_at = clock()
        self.run_id = run_id or new_run_id(self._started_at)
        self._stages: dict[StageName, StageMetrics] = {
            name: StageMetrics(stage=name, status=StageStatus.PENDING) for name in StageName
        }
        self._sources: tuple[SourceMetrics, ...] = ()
        self._articles: dict[str, ArticleMetrics] = {}
        self._llm_calls: list[LLMCallMetrics] = []
        self._findings: tuple[FindingMetrics, ...] = ()
        self._published = (0, 0)
        self._sensitive: list[str] = []
        self._result: RunMetrics | None = None
        self.sink_error: Exception | None = None
        """Set when the sink failed while another exception was already propagating."""

    # -- lifecycle ------------------------------------------------------
    def __enter__(self) -> RunRecorder:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if self._result is None:
            if exc is None:
                self.finish()
            else:
                self._close_after(exc)
        if self._sink is not None and self._result is not None:
            try:
                self._sink(self._result)
            except Exception as sink_exc:
                if exc is None:
                    raise
                self.sink_error = sink_exc
        return False

    def finish(self, status: RunStatus | None = None) -> RunMetrics:
        """End the run. Without `status`, it is derived from the stages."""
        if self._result is not None:
            return self._result
        now = self._clock()
        for name, stage in self._stages.items():
            if stage.status is StageStatus.RUNNING:
                self._stages[name] = stage.fail(
                    max(now, stage.started_at or now),
                    ErrorRecord(kind=ErrorKind.UNEXPECTED, detail="stage left running at finish"),
                )
            elif stage.status is StageStatus.PENDING:
                self._stages[name] = stage.skip()
        final = status or derive_status(self._stages.values())
        self._result = self.snapshot().finish(final, max(now, self._started_at))
        return self._result

    @property
    def result(self) -> RunMetrics | None:
        return self._result

    def _close_after(self, exc: BaseException) -> None:
        interrupted = not isinstance(exc, Exception) or isinstance(exc, (RunAborted, TimeoutError, asyncio.TimeoutError))
        error = classify_exception(exc)
        now = self._clock()
        for name, stage in self._stages.items():
            if stage.status is StageStatus.RUNNING:
                self._stages[name] = stage.fail(max(now, stage.started_at or now), error)
        has_failed = any(s.status is StageStatus.FAILED for s in self._stages.values())
        # An exception raised outside every stage leaves no failed stage to
        # carry it, and `failed` requires one; the run stopped early, so it is
        # recorded as aborted.
        self.finish(RunStatus.ABORTED if interrupted or not has_failed else RunStatus.FAILED)

    # -- stages ---------------------------------------------------------
    @contextmanager
    def stage(self, name: StageName) -> Iterator[None]:
        """Time one stage. An exception fails the stage and is re-raised."""
        self._stages[name] = self._stages[name].start(self._clock())
        try:
            yield
        except BaseException as exc:
            current = self._stages[name]
            if current.status is StageStatus.RUNNING:
                self._stages[name] = current.fail(self._after(current), classify_exception(exc))
            raise
        else:
            current = self._stages[name]
            self._stages[name] = current.succeed(self._after(current))

    def skip(self, name: StageName) -> None:
        self._stages[name] = self._stages[name].skip()

    def fail(self, name: StageName, error: ErrorRecord) -> None:
        """Fail a stage that is running, for a failure the flow handles without raising."""
        current = self._stages[name]
        if current.status is StageStatus.PENDING:
            current = current.start(self._clock())
        self._stages[name] = current.fail(self._after(current), error)

    def stage_status(self, name: StageName) -> StageStatus:
        return self._stages[name].status

    def _after(self, stage: StageMetrics) -> datetime:
        now = self._clock()
        return max(now, stage.started_at) if stage.started_at is not None else now

    # -- facts ----------------------------------------------------------
    def record_sources(self, sources: Iterable[SourceMetrics]) -> None:
        self._sources = tuple(sources)

    def record_article(self, metrics: ArticleMetrics) -> None:
        """Add or replace one article's record (a later stage may add its decision)."""
        self._articles[metrics.article_id] = metrics

    def add_llm_call(self, metrics: LLMCallMetrics) -> None:
        self._llm_calls.append(metrics)

    def record_findings(self, findings: Iterable[FindingMetrics]) -> None:
        self._findings = tuple(findings)

    def set_published(self, must_read: int, worth_knowing: int) -> None:
        self._published = (must_read, worth_knowing)

    def mark_sensitive(self, *texts: str) -> None:
        """Texts that must not appear in the record: bodies, prompts, model output."""
        self._sensitive.extend(text for text in texts if text)

    @property
    def sensitive_texts(self) -> tuple[str, ...]:
        return tuple(self._sensitive)

    def snapshot(self) -> RunMetrics:
        """The record so far, with the run still running."""
        return RunMetrics(
            run_id=self.run_id,
            status=RunStatus.RUNNING,
            started_at=self._started_at,
            stages=tuple(self._stages.values()),
            sources=self._sources,
            articles=tuple(self._articles.values()),
            llm_calls=tuple(self._llm_calls),
            judge_findings=self._findings,
            published_must_read_count=self._published[0],
            published_worth_knowing_count=self._published[1],
        )
