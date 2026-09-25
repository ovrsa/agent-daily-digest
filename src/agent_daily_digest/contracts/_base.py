"""Shared base model, constrained scalar types, and helpers."""

from __future__ import annotations

import hashlib
from collections.abc import Hashable, Iterable
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints


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
