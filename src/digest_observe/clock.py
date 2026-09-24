"""The clock the recorders read, so tests can drive time."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

Clock = Callable[[], datetime]
"""Returns the current time as a timezone-aware datetime."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
