"""Fakes for the observability tests: a clock that advances on every read, and a price table."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_daily_digest.observe import ModelPrice, PricingTable

START = datetime(2026, 9, 24, 23, 0, 0, tzinfo=timezone.utc)

PRICING = PricingTable(
    prices={
        "claude-sonnet-5": ModelPrice(input=2.0, output=10.0, cache_write=2.5, cache_read=0.2),
        "claude-haiku-4-5": ModelPrice(input=1.0, output=5.0, cache_write=1.25, cache_read=0.1),
    }
)


class FakeClock:
    """Each call returns a time `step` later than the previous one."""

    def __init__(self, start: datetime = START, step: timedelta = timedelta(milliseconds=250)) -> None:
        self.now = start
        self.step = step
        self.reads = 0

    def __call__(self) -> datetime:
        current = self.now
        self.now = self.now + self.step
        self.reads += 1
        return current
