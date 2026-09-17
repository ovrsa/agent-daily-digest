"""Deterministic gate results with machine-readable exclusion reasons."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType

from pydantic import Field, StrictBool, model_validator

from ._base import ArticleId, ContractModel


class GateName(str, Enum):
    REQUIRED_FIELDS = "required_fields"
    """URL, publication date and required fields are verifiable."""
    CONTENT_AVAILABLE = "content_available"
    """The body, or enough primary information in the feed, is available."""
    NOT_PREVIOUSLY_PROCESSED = "not_previously_processed"
    """The URL or body hash is not in the processing state."""
    NOT_KNOWN_DUPLICATE = "not_known_duplicate"
    """The article is not only a restatement of an already seen article."""


GATE_ORDER: tuple[GateName, ...] = tuple(GateName)


class GateExclusionReason(str, Enum):
    MISSING_URL = "missing_url"
    INVALID_URL = "invalid_url"
    MISSING_TITLE = "missing_title"
    MISSING_PUBLISHED_AT = "missing_published_at"
    INVALID_PUBLISHED_AT = "invalid_published_at"
    """`published_at` is present but cannot be parsed into a timezone-aware date-time."""
    BODY_FETCH_FAILED = "body_fetch_failed"
    """Fetching failed and the feed lacks enough primary information."""
    BODY_EXTRACTION_FAILED = "body_extraction_failed"
    """Extraction failed and the feed lacks enough primary information."""
    ALREADY_PROCESSED_URL = "already_processed_url"
    ALREADY_PROCESSED_CONTENT_HASH = "already_processed_content_hash"
    DUPLICATE_OF_KNOWN_ARTICLE = "duplicate_of_known_article"


_R = GateExclusionReason
GATE_REASONS: Mapping[GateName, frozenset[GateExclusionReason]] = MappingProxyType(
    {
        GateName.REQUIRED_FIELDS: frozenset(
            {
                _R.MISSING_URL,
                _R.INVALID_URL,
                _R.MISSING_TITLE,
                _R.MISSING_PUBLISHED_AT,
                _R.INVALID_PUBLISHED_AT,
            }
        ),
        GateName.CONTENT_AVAILABLE: frozenset({_R.BODY_FETCH_FAILED, _R.BODY_EXTRACTION_FAILED}),
        GateName.NOT_PREVIOUSLY_PROCESSED: frozenset(
            {_R.ALREADY_PROCESSED_URL, _R.ALREADY_PROCESSED_CONTENT_HASH}
        ),
        GateName.NOT_KNOWN_DUPLICATE: frozenset({_R.DUPLICATE_OF_KNOWN_ARTICLE}),
    }
)


class GateResult(ContractModel):
    gate: GateName
    passed: StrictBool
    reason: GateExclusionReason | None = None

    @model_validator(mode="after")
    def _check_reason(self) -> GateResult:
        if self.passed:
            if self.reason is not None:
                raise ValueError("a passed gate must not carry a reason")
        elif self.reason is None:
            raise ValueError("a failed gate requires a reason")
        elif self.reason not in GATE_REASONS[self.gate]:
            raise ValueError("reason does not belong to this gate")
        return self


class GateOutcome(ContractModel):
    """Gate results for one article, in `GATE_ORDER` from the first gate without gaps.

    Evaluation either stops right after the first failed gate or runs every
    gate. An article passes only when every gate was evaluated and passed.
    """

    article_id: ArticleId
    results: tuple[GateResult, ...] = Field(min_length=1)

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    @property
    def exclusion_reasons(self) -> tuple[GateExclusionReason, ...]:
        return tuple(r.reason for r in self.results if r.reason is not None)

    @model_validator(mode="after")
    def _check_results(self) -> GateOutcome:
        gates = tuple(result.gate for result in self.results)
        if gates != GATE_ORDER[: len(gates)]:
            raise ValueError("gates must follow GATE_ORDER from the first gate without gaps or repeats")
        if len(gates) < len(GATE_ORDER):
            *evaluated, last = self.results
            if last.passed or not all(result.passed for result in evaluated):
                raise ValueError("a partial evaluation must stop right after the first failed gate")
        return self
