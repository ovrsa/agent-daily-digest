"""Run every enabled source and report what each one produced.

One source failing never stops another: each is timed and caught on its own,
and the failure is recorded as an `ErrorRecord` on that source's result.
"""

from __future__ import annotations

import datetime as dt
import time

from pydantic import BaseModel, ConfigDict, Field

from agent_daily_digest.contracts import (
    CollectedItem,
    ErrorRecord,
    SourceFetchResult,
    SourceFetchStatus,
    SourceId,
    SourceKind,
    SourceMetrics,
)

from .config import CollectionConfig, SourceSpec
from .connectors import CONNECTORS, CollectContext, ProbeRecorder, dedupe
from .transport import Fetcher, classify_failure


class SourceProbeStats(BaseModel):
    """What one source spent on extra head-metadata requests.

    Sitemap and blog-index connectors probe; other sources report zeroes.
    `SourceMetrics` has no field for this, and #3 owns the contracts, so the
    counts stay in the collection layer. #9 decides whether run metrics need
    them.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: SourceId
    attempted: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped_over_cap: int = Field(ge=0)
    """Candidates left unprobed by the cap (blog-index dates are not known yet)."""
    duration_ms: int = Field(ge=0)
    """Not counted in `SourceFetchResult.duration_ms`; the two do not overlap."""


class CollectionReport(BaseModel):
    """Everything one collection pass produced."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    results: tuple[SourceFetchResult, ...] = ()
    probes: tuple[SourceProbeStats, ...] = ()

    @property
    def metrics(self) -> tuple[SourceMetrics, ...]:
        return tuple(result.to_metrics() for result in self.results)

    @property
    def items(self) -> tuple[CollectedItem, ...]:
        return tuple(item for result in self.results for item in result.items)

    @property
    def failed_source_ids(self) -> tuple[str, ...]:
        return tuple(
            result.source_id
            for result in self.results
            if result.status is SourceFetchStatus.FAILED
        )

    def result(self, source_id: str) -> SourceFetchResult:
        for result in self.results:
            if result.source_id == source_id:
                return result
        raise KeyError(source_id)

    def probe(self, source_id: str) -> SourceProbeStats:
        for stats in self.probes:
            if stats.source_id == source_id:
                return stats
        raise KeyError(source_id)


def collect_source(
    spec: SourceSpec,
    *,
    fetcher: Fetcher,
    now: dt.datetime,
    window: dt.timedelta,
    max_items: int,
    known_urls: frozenset[str] = frozenset(),
) -> tuple[SourceFetchResult, SourceProbeStats]:
    """Collect one source. Never raises for a fetch or parse failure."""
    probe = ProbeRecorder()
    context = CollectContext(
        fetcher=fetcher,
        now=now,
        window=window,
        max_items=max_items,
        probe=probe,
        known_urls=known_urls,
    )
    started = time.monotonic_ns()
    try:
        items = dedupe(CONNECTORS[spec.connector](spec, context))[:max_items]
        result = _result(spec, SourceFetchStatus.SUCCEEDED, started, probe, items=items)
    except Exception as exc:
        result = _result(
            spec, SourceFetchStatus.FAILED, started, probe, failure=classify_failure(exc)
        )
    return result, _stats(spec, probe)


def collect_all(
    config: CollectionConfig,
    *,
    fetcher: Fetcher,
    now: dt.datetime,
    known_urls: frozenset[str] = frozenset(),
) -> CollectionReport:
    """Collect every enabled source in registry order.

    `known_urls` are canonical URLs already in the processing state. The
    collector does not read that state itself: #5 and #10 own it, and until
    #10 passes a value this defaults to empty.
    """
    results = []
    probes = []
    for spec in config.enabled_sources:
        result, stats = collect_source(
            spec,
            fetcher=fetcher,
            now=now,
            window=dt.timedelta(days=config.window_days_for(spec)),
            max_items=config.items_for(spec),
            known_urls=known_urls,
        )
        results.append(result)
        probes.append(stats)
    return CollectionReport(results=tuple(results), probes=tuple(probes))


def _result(
    spec: SourceSpec,
    status: SourceFetchStatus,
    started_ns: int,
    probe: ProbeRecorder,
    *,
    items: tuple[CollectedItem, ...] = (),
    failure: ErrorRecord | None = None,
) -> SourceFetchResult:
    elapsed_ns = max(0, time.monotonic_ns() - started_ns - probe.duration_ns)
    return SourceFetchResult(
        source_id=spec.id,
        source_kind=SourceKind(spec.kind),
        status=status,
        items=items,
        duration_ms=elapsed_ns // 1_000_000,
        failure=failure,
    )


def _stats(spec: SourceSpec, probe: ProbeRecorder) -> SourceProbeStats:
    return SourceProbeStats(
        source_id=spec.id,
        attempted=probe.attempted,
        succeeded=probe.succeeded,
        failed=probe.failed,
        skipped_over_cap=probe.skipped_over_cap,
        duration_ms=probe.duration_ms,
    )
