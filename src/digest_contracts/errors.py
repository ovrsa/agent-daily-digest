"""Error records that are safe to keep in run metrics."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import StringConstraints, ValidationError

from ._base import ContractModel, NonBlankStr

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
