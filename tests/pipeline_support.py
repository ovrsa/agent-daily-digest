"""A day's worth of sources, pages and model answers for the pipeline tests. No network, no model, no git.

Three items come in: `a001` (the retry-budget article, researched to
`complete`), `a002` (an evaluation-method post that links to its results,
researched to `insufficient`) and `a003` (no URL, dropped at the gates). The
model answers by role, told apart by the system prompt of each request.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from digest_collect import CollectionReport
from digest_contracts import ErrorKind, ProcessedState, SourceFetchResult
from digest_judge import SYSTEM_PROMPT as JUDGE_SYSTEM_PROMPT
from digest_normalize import FetchedPage, load_state
from digest_observe import LLMResponse, MetricsStore, StageFailed
from digest_pipeline import Models, Paths, Pipeline
from digest_research import SYSTEM_PROMPT as RESEARCH_SYSTEM_PROMPT
from digest_select import SYSTEM_PROMPT as SELECTOR_SYSTEM_PROMPT
from normalize_helpers import StubFetcher, page
from observe_support import PRICING
from research_support import USAGE, complete_map

ROOT = Path(__file__).resolve().parents[1]
DIGEST_DATE = date(2026, 9, 25)
NOW = datetime(2026, 9, 25, 7, 0, tzinfo=timezone.utc)
URL_A = "https://example.com/posts/retry-budget"
URL_B = "https://example.com/posts/eval-method"
RESULTS_URL = "https://github.com/example/harness-eval/blob/main/RESULTS.md"
MODELS = Models(research="claude-sonnet-5", selector="claude-sonnet-5", judge="claude-sonnet-5")

EVAL_PAGE = f"""<html><head><title>Eval method</title></head><body><article>
<h1>How we evaluate our coding agent</h1>
<p>We describe the evaluation method we use for our coding agent harness, including the task suite, the scoring rules and the way we separate flaky runs from real regressions.</p>
<p>The full results table lives in <a href="{RESULTS_URL}">the results file</a>, which we update after every release of the harness.</p>
</article></body></html>"""

A001_IDS = ("a001#main/p2#1", "a001#main/p5#1", "a001#main/p7#1")
"""The Evidence IDs research mints for `complete_map`: statement, code, comparison."""

SCORES = {"practicality": 4, "specificity_reproducibility": 4, "novelty": 3, "source_reliability": 4, "reader_impact": 4, "read_original_value": 4}
LOW = {k: 2 for k in SCORES}


def item(article_id: str, url: str | None, title: str) -> dict[str, Any]:
    return {
        "article_id": article_id,
        "source_id": "simonw",
        "source_kind": "fixed_watch",
        "title": title,
        "url": url,
        "published_at": "2026-09-24T06:00:00+00:00",
        "feed_summary": None,
    }


ITEMS = (
    item("a001", URL_A, "Harness retry budget"),
    item("a002", URL_B, "How we evaluate our coding agent"),
    item("a003", None, "An item without a link"),
)


def report(*, items: Sequence[dict[str, Any]] = ITEMS, failed: bool = False) -> CollectionReport:
    if failed:
        result = SourceFetchResult.model_validate(
            {"source_id": "simonw", "source_kind": "fixed_watch", "status": "failed", "duration_ms": 5, "failure": {"kind": "network"}}
        )
    else:
        result = SourceFetchResult.model_validate(
            {"source_id": "simonw", "source_kind": "fixed_watch", "status": "succeeded", "items": list(items), "duration_ms": 5}
        )
    return CollectionReport(results=(result,))


def fetcher() -> StubFetcher:
    return StubFetcher(
        {
            URL_A: page("article_basic", url=URL_A),
            URL_B: FetchedPage(requested_url=URL_B, final_url=URL_B, html=EVAL_PAGE, content_type="text/html"),
        }
    )


def included_entry(**changes: Any) -> dict[str, Any]:
    entry = {
        "what_happened": {"text": "著者はステージごとの再試行を3回に制限した。", "evidence_ids": [A001_IDS[0], A001_IDS[1]]},
        "why_read": "再試行の上限をどう置くかを決める材料になる。",
        "evidence": {"text": "RetryBudget(max_attempts=3) の設定と、select の所要時間が9.2秒から6.1秒に減った比較。", "evidence_ids": [A001_IDS[1], A001_IDS[2]]},
    }
    entry.update(changes)
    return entry


def decision(*, adopt: bool = True) -> dict[str, Any]:
    included = [{"article_id": "a001", "scores": SCORES, "decision_reason": "設定と比較の数値がそろう。", "entry": included_entry()}]
    excluded = [{"article_id": "a002", "scores": LOW, "decision_reason": "結果は別ページにあり、本文に具体的な根拠が無い。"}]
    if not adopt:
        excluded.append({"article_id": "a001", "scores": LOW, "decision_reason": "今日は採用しない。"})
        included = []
    return {"must_read": included, "worth_knowing": [], "excluded": excluded, "duplicate_groups": []}


def finding(**assessment: Any) -> dict[str, Any]:
    return {
        "finding_id": "J1",
        "article_id": "a001",
        "assessment": {
            "category": "groundedness",
            "severity": "medium",
            "problem": "比較の条件（実行回数）が書かれていない。",
            "source_evidence": "select | 9.2s | 6.1s",
            "confidence": "medium",
        }
        | assessment,
        "improvement": {"suggested_fix": "留保に、測定した実行の件数が書かれていないことを足す。"},
    }


class RoutedModel:
    """Answers each request by the role its system prompt names, in order per role.

    An answer that is an exception is raised instead of returned. Research
    answers are keyed by the article id in the prompt; an article with no
    answer left gets an empty map, which research turns into `insufficient`.
    """

    def __init__(
        self,
        *,
        research: dict[str, list[Any]] | None = None,
        selector: Sequence[Any] = (),
        judge: Sequence[Any] = (),
    ) -> None:
        self.research = {key: list(values) for key, values in (research or {"a001": [complete_map()]}).items()}
        self.selector = list(selector)
        self.judge = list(judge)
        self.requests: dict[str, list[Any]] = {"research": [], "selector": [], "judge": []}

    def __call__(self, request: Any) -> LLMResponse:
        if request.system_prompt == RESEARCH_SYSTEM_PROMPT:
            self.requests["research"].append(request)
            article_id = re.search(r"article_id: (\S+)", request.prompt)[1]
            queue = self.research.get(article_id)
            answer = queue.pop(0) if queue else {}
        elif request.system_prompt == SELECTOR_SYSTEM_PROMPT:
            self.requests["selector"].append(request)
            answer = self.selector.pop(0)
        elif request.system_prompt == JUDGE_SYSTEM_PROMPT:
            self.requests["judge"].append(request)
            answer = self.judge.pop(0)
        else:
            raise AssertionError("a request for an unknown role")
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, LLMResponse):
            return answer
        return LLMResponse(structured_output=answer, usage=USAGE)


@dataclass
class FakePublisher:
    """Records what a run asks of GitHub. `fail_on` names the call that raises."""

    fail_on: str | None = None
    failure: BaseException = field(default_factory=lambda: StageFailed(ErrorKind.NETWORK, "git push exited 1"))
    commit: str = "c0ffee0000000000000000000000000000000001"
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def sync(self) -> None:
        self._call("sync", None)

    def publish(self, paths: Sequence[Path], message: str) -> str:
        self._call("publish", (tuple(paths), message))
        return self.commit

    def comment(self, commit: str, body: str) -> None:
        self._call("comment", (commit, body))

    def _call(self, name: str, args: Any) -> None:
        self.calls.append((name, args))
        if self.fail_on == name:
            raise self.failure

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def workspace(tmp_path: Path) -> Paths:
    """A `digests/` with the real README index, an empty state, and a log directory. Idempotent."""
    digests = tmp_path / "digests"
    if not digests.exists():
        digests.mkdir()
        shutil.copy2(ROOT / "digests" / "README.md", digests / "README.md")
    return Paths(digests_dir=digests, state_path=tmp_path / "state" / "processed.json", judge_log_dir=tmp_path / "logs" / "judge")


def pipeline(
    tmp_path: Path,
    model: RoutedModel,
    publisher: FakePublisher | None = None,
    *,
    collect: Any = None,
    fetch: Any = None,
) -> Pipeline:
    return Pipeline(
        collect=collect or (lambda known: report()),
        fetch=fetch or fetcher(),
        invoke=model,
        pricing=PRICING,
        models=MODELS,
        publisher=publisher or FakePublisher(),
        paths=workspace(tmp_path),
        store=MetricsStore(tmp_path / "metrics", clock=lambda: NOW),
        clock=lambda: NOW,
    )


def state_of(tmp_path: Path) -> ProcessedState:
    return load_state(tmp_path / "state" / "processed.json")
