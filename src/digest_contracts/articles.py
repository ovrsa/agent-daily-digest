"""Collected items, per-source fetch results, normalized articles and processing state."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import AwareDatetime, model_validator

from ._base import (
    ArticleId,
    ContentHash,
    ContractModel,
    HttpUrlStr,
    NonBlankStr,
    NonNegativeInt,
    SourceId,
    compute_content_hash,
    first_duplicate,
)
from .errors import ErrorRecord
from .selection import Decision


class SourceKind(str, Enum):
    FIXED_WATCH = "fixed_watch"
    """定点観測: official blogs, expert blogs, newsletters, releases, papers."""
    DISCOVERY = "discovery"
    """発見経路: Hacker News, Reddit, surveys."""


class SourceFetchStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class BodySource(str, Enum):
    EXTRACTED = "extracted"
    """Body extracted from the original page."""
    FEED_FALLBACK = "feed_fallback"
    """Extraction failed but the feed carried enough primary information."""


class CollectedItem(ContractModel):
    """One candidate as a source returned it, before any gate.

    Web content is untrusted input. `title`, `url` and `published_at` keep the
    raw values, optional and unvalidated here, so the `required_fields` gate
    can record why an item was dropped instead of the collector failing on it.
    That gate parses `published_at` into the timezone-aware
    `NormalizedArticle.published_at` and records `invalid_published_at` when
    it cannot.
    """

    article_id: ArticleId
    source_id: SourceId
    source_kind: SourceKind
    title: str | None = None
    url: str | None = None
    published_at: str | None = None
    feed_summary: str | None = None


def _check_source_outcome(
    status: SourceFetchStatus, failure: ErrorRecord | None, item_count: int
) -> None:
    if status is SourceFetchStatus.FAILED:
        if failure is None:
            raise ValueError("a failed source requires `failure`")
        if item_count:
            raise ValueError("a failed source must not report items")
    elif failure is not None:
        raise ValueError("a succeeded source must not carry `failure`")


class SourceMetrics(ContractModel):
    """Per-source counts kept in run metrics, without the collected items."""

    source_id: SourceId
    source_kind: SourceKind
    status: SourceFetchStatus
    item_count: NonNegativeInt
    duration_ms: NonNegativeInt
    failure: ErrorRecord | None = None

    @model_validator(mode="after")
    def _check_outcome(self) -> SourceMetrics:
        _check_source_outcome(self.status, self.failure, self.item_count)
        return self


class SourceFetchResult(ContractModel):
    """Result of collecting one source. One failing source does not fail the others."""

    source_id: SourceId
    source_kind: SourceKind
    status: SourceFetchStatus
    items: tuple[CollectedItem, ...] = ()
    duration_ms: NonNegativeInt
    failure: ErrorRecord | None = None

    @property
    def item_count(self) -> int:
        return len(self.items)

    @model_validator(mode="after")
    def _check_items(self) -> SourceFetchResult:
        _check_source_outcome(self.status, self.failure, self.item_count)
        for item in self.items:
            if item.source_id != self.source_id or item.source_kind is not self.source_kind:
                raise ValueError("every item must belong to this source")
        if first_duplicate(item.article_id for item in self.items) is not None:
            raise ValueError("article_id must be unique within a source")
        return self

    def to_metrics(self) -> SourceMetrics:
        return SourceMetrics(
            source_id=self.source_id,
            source_kind=self.source_kind,
            status=self.status,
            item_count=self.item_count,
            duration_ms=self.duration_ms,
            failure=self.failure,
        )


class NormalizedArticle(ContractModel):
    """An article that passed field validation and has a normalized body.

    The body lives only for the duration of a run. It must never be written
    to Git; `ProcessedRecord` is the only persisted shape.
    """

    article_id: ArticleId
    source_id: SourceId
    source_kind: SourceKind
    canonical_url: HttpUrlStr
    title: NonBlankStr
    author: NonBlankStr | None = None
    published_at: AwareDatetime
    body_source: BodySource
    body_text: NonBlankStr
    content_hash: ContentHash

    @property
    def char_count(self) -> int:
        return len(self.body_text)

    @model_validator(mode="after")
    def _check_hash(self) -> NormalizedArticle:
        if self.content_hash != compute_content_hash(self.body_text):
            raise ValueError("content_hash must be compute_content_hash(body_text)")
        return self


class ProcessedRecord(ContractModel):
    """Git-tracked processing state. Exactly these four fields and nothing else."""

    canonical_url: HttpUrlStr
    first_seen_at: AwareDatetime
    content_hash: ContentHash
    last_decision: Decision


class ProcessedState(ContractModel):
    schema_version: Literal[1] = 1
    records: tuple[ProcessedRecord, ...] = ()

    @model_validator(mode="after")
    def _check_unique(self) -> ProcessedState:
        if first_duplicate((r.canonical_url, r.content_hash) for r in self.records) is not None:
            raise ValueError("(canonical_url, content_hash) must be unique")
        return self
