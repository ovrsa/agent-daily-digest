"""Shared base model, constrained scalar types, and helpers."""

from __future__ import annotations

import hashlib
from collections.abc import Hashable, Iterable
from typing import Annotated
from urllib.parse import urlsplit
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints
from enum import Enum
from pydantic import StringConstraints, ValidationError



class ContractModel(BaseModel):
    """Base for every contract.

    Unknown keys are rejected (JSON Schema `additionalProperties: false`) and
    instances are frozen, so a status change has to go through the model's
    transition methods instead of attribute assignment.
    Do not change a status with `model_copy(update=...)`; it skips validation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class InvalidTransitionError(ValueError):
    """Raised when a status change is not in the allowed transition table."""


def _check_http_url(value: str) -> str:
    if any(ch.isspace() for ch in value):
        raise ValueError("URL must not contain whitespace")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("absolute http(s) URL required")
    # Canonical URLs are written to the Git-tracked processing state.
    if parts.username is not None or parts.password is not None:
        raise ValueError("URL must not contain credentials")
    return value


NonBlankStr = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
"""Text with at least one non-whitespace character. The value is not stripped."""

ArticleId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
"""Join key for one collected item across gates, selection, judge and metrics."""

SourceId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
"""Source key as written in `config.json` (for example `simonw`)."""

ContentHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
"""Lowercase hex SHA-256 of the normalized body, see `compute_content_hash`."""

HttpUrlStr = Annotated[
    str, StringConstraints(min_length=1, max_length=2048), AfterValidator(_check_http_url)
]
"""Absolute http(s) URL kept exactly as given.

Pydantic's `HttpUrl` rewrites values (for example it appends `/` to a bare
host), which would change canonical URLs and break duplicate detection.
Canonicalization belongs to normalization (#5), not to the contract.
"""

NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
"""Integer count or duration. `True`, `1.0` and `"1"` are rejected."""


def compute_content_hash(body_text: str) -> str:
    """Return the content hash stored in `NormalizedArticle.content_hash`."""
    return hashlib.sha256(body_text.encode("utf-8")).hexdigest()


def first_duplicate(keys: Iterable[Hashable]) -> Hashable | None:
    seen: set[Hashable] = set()
    for key in keys:
        if key in seen:
            return key
        seen.add(key)
    return None


ERROR_DETAIL_MAX_CHARS = 500
_LOC_MAX_CHARS = 200
_EXTRA_KEY_TOKEN = "<extra>"


class ErrorKind(str, Enum):
    NETWORK = "network"
    TIMEOUT = "timeout"
    HTTP_STATUS = "http_status"
    PARSE = "parse"
    EXTRACTION = "extraction"
    VALIDATION = "validation"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    CANCELLED = "cancelled"
    UNEXPECTED = "unexpected"


class ErrorRecord(ContractModel):
    """A classified failure.

    `detail` is a short operator note. Callers must not put article bodies,
    model input or output, or secrets in it; the length cap limits the damage
    when that rule is broken.
    """

    kind: ErrorKind
    detail: Annotated[NonBlankStr, StringConstraints(max_length=ERROR_DETAIL_MAX_CHARS)] | None = None


class ValidationIssue(ContractModel):
    """Location and error type of one Pydantic validation error, without the input."""

    loc: Annotated[NonBlankStr, StringConstraints(max_length=_LOC_MAX_CHARS)]
    type: Annotated[NonBlankStr, StringConstraints(max_length=100)]

    @classmethod
    def from_validation_error(cls, error: ValidationError) -> tuple[ValidationIssue, ...]:
        # `include_input=False` keeps the rejected model output out of metrics.
        # Messages are dropped too, because custom validator messages may quote values.
        return tuple(
            cls(loc=_safe_loc(item["loc"], item["type"]), type=item["type"])
            for item in error.errors(include_url=False, include_context=False, include_input=False)
        )


def _safe_loc(parts: tuple[int | str, ...], error_type: str) -> str:
    # The last part of an `extra_forbidden` loc is a key the model wrote, so it
    # can be any length, blank, or an instruction; it must not reach metrics.
    if error_type == "extra_forbidden" and parts:
        parts = (*parts[:-1], _EXTRA_KEY_TOKEN)
    loc = ".".join(str(part) for part in parts)[:_LOC_MAX_CHARS]
    return loc if loc and not loc.isspace() else "__root__"
