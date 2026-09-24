"""Shared inputs for the normalization tests.

No test reaches the network: every fetch goes through `StubFetcher`. The HTML
fixtures are synthetic and contain no real article body.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import factories as f
from digest_contracts import CollectedItem, ErrorKind, ProcessedRecord, ProcessedState
from digest_normalize import FetchedPage, FetchFailure

HTML_DIR = Path(__file__).parent / "fixtures" / "html"

FEED_URL = "https://example.com/posts/retry-budget"


def read_html(name: str) -> str:
    return (HTML_DIR / f"{name}.html").read_text(encoding="utf-8")


def page(name: str, *, url: str = FEED_URL, final_url: str | None = None) -> FetchedPage:
    return FetchedPage(
        requested_url=url,
        final_url=final_url or url,
        html=read_html(name),
        content_type="text/html; charset=utf-8",
    )


class StubFetcher:
    """Returns a prepared result per URL and records what was requested."""

    def __init__(self, results: dict[str, FetchedPage | FetchFailure] | None = None) -> None:
        self.results = results or {}
        self.requested: list[str] = []

    def __call__(self, url: str) -> FetchedPage | FetchFailure:
        self.requested.append(url)
        try:
            return self.results[url]
        except KeyError:
            return FetchFailure(kind=ErrorKind.NETWORK, detail="no stub registered")


def collected(**overrides: Any) -> CollectedItem:
    data = f.collected_item(**{"url": FEED_URL, **overrides})
    return CollectedItem.model_validate(data)


def feed_summary(chars: int) -> str:
    """A synthetic feed summary of exactly `chars` characters."""
    unit = "The post reports a measured change in the agent harness. "
    return (unit * (chars // len(unit) + 1))[:chars]


def state_with(*records: ProcessedRecord) -> ProcessedState:
    return ProcessedState(records=records)


def processed_record(**overrides: Any) -> ProcessedRecord:
    return ProcessedRecord.model_validate(f.processed_record(**overrides))
