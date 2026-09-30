"""Articles, a scripted model and a scripted web for the research tests. No network, no model."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent_daily_digest.contracts import BodySource, ErrorKind, NormalizedArticle, SourceKind, compute_content_hash
from agent_daily_digest.content.fetch import FetchedPage, FetchFailure
from agent_daily_digest.content.extract import extract_document
from agent_daily_digest.observe import LLMResponse, ModelUsage
from normalize_helpers import read_html

PUBLISHED = datetime(2026, 9, 24, 6, 0, tzinfo=timezone.utc)
RESULTS_URL = "https://github.com/example/harness-eval/blob/main/RESULTS.md"
USAGE = (ModelUsage(model="claude-sonnet-5", input_tokens=10, output_tokens=200, cache_read_input_tokens=19_000),)


def article(
    article_id: str = "a001",
    *,
    fixture: str = "article_basic",
    body: str | None = None,
    url: str | None = None,
    title: str = "Harness retry budget",
    source_id: str = "simonw",
    kind: SourceKind = SourceKind.FIXED_WATCH,
    author: str | None = "Synthetic Author",
    published_at: datetime = PUBLISHED,
) -> NormalizedArticle:
    text = body if body is not None else extract_document(read_html(fixture)).body_text
    return NormalizedArticle(
        article_id=article_id,
        source_id=source_id,
        source_kind=kind,
        canonical_url=url or f"https://example.com/posts/{article_id}",
        title=title,
        author=author,
        published_at=published_at,
        body_source=BodySource.EXTRACTED,
        body_text=text,
        content_hash=compute_content_hash(text),
    )


class ScriptedModel:
    """Returns the given structured outputs in order and keeps every request it saw."""

    def __init__(self, *outputs: Any) -> None:
        self.outputs = list(outputs)
        self.requests: list[Any] = []

    def __call__(self, request: Any) -> LLMResponse:
        self.requests.append(request)
        output = self.outputs.pop(0)
        if isinstance(output, LLMResponse):
            return output
        return LLMResponse(structured_output=output, usage=USAGE)


class ScriptedWeb:
    """Serves pages by URL and records every URL asked for."""

    def __init__(self, pages: dict[str, str] | None = None) -> None:
        self.pages = pages or {}
        self.requested: list[str] = []

    def __call__(self, url: str):
        self.requested.append(url)
        if url not in self.pages:
            return FetchFailure(kind=ErrorKind.HTTP_STATUS, detail="HTTP 404")
        return FetchedPage(requested_url=url, final_url=url, html=self.pages[url], content_type="text/html")


def complete_map() -> dict[str, Any]:
    """A round-one output that satisfies every condition for `article_basic`."""
    return {
        "evidence": [
            {"id": "e1", "doc": "main", "paragraph": "p2", "kind": "statement", "quote": "We capped agent retries at `3` per stage"},
            {"id": "e2", "doc": "main", "paragraph": "p5", "kind": "code", "quote": "budget = RetryBudget(max_attempts=3)"},
            {"id": "e3", "doc": "main", "paragraph": "p7", "kind": "comparison", "quote": "select | 9.2s | 6.1s"},
        ],
        "claims": [
            {"id": "c1", "kind": "what_happened", "text": "著者はステージごとの再試行を3回に制限した。", "evidence": ["e1", "e2"], "numeric": False, "reproducible": False, "areas": ["failure_recovery"]},
            {"id": "c2", "kind": "finding", "text": "select の所要時間が 9.2 秒から 6.1 秒に減った。", "evidence": ["e3"], "numeric": True, "conditions": ["e3"], "reproducible": False},
        ],
        "concepts": [{"name": "retry budget", "area": "failure_recovery", "evidence": ["e2"]}],
        "limitations": [{"text": "測定した実行の件数は書かれていない。"}],
        "open_questions": [],
    }


def statement_only_map(**extra: Any) -> dict[str, Any]:
    """What happened is supported, but nothing concrete: insufficient."""
    data: dict[str, Any] = {
        "evidence": [{"id": "e1", "doc": "main", "paragraph": "p2", "kind": "statement", "quote": "We compared two retry policies."}],
        "claims": [{"id": "c1", "kind": "what_happened", "text": "著者は2つの再試行方針を比べた。", "evidence": ["e1"], "numeric": False, "reproducible": False}],
    }
    data.update(extra)
    return data


RESULTS_PAGE = """<html><body><article>
<h1>Results</h1>
<p>Policy A finished 212 of 240 tasks and policy B finished 229 of 240 tasks on the same 240-task suite with the same model.</p>
<pre><code>policy_b = RetryPolicy(max_attempts=3, backoff=None)</code></pre>
<p>Each task ran once per policy on the same commit, and failures were counted when the harness gave up after its last retry.</p>
</article></body></html>"""
