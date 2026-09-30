"""The source registry, read from `config.json`.

The `collection` block of `config.json` is the registry. The keys the
retired `src/fetch.py` read (`sources` / `reddit_subs` / `gh_repos`) were
removed with it in #11.

Every source declares its `kind`, which is how 定点観測 (`fixed_watch`) and
発見経路 (`discovery`) are told apart, and its `connector`, which decides how
it is fetched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from agent_daily_digest.contracts import HttpUrlStr, NonBlankStr, SourceId, SourceKind
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

from agent_daily_digest.collect.transport import Fetcher, classify_failure


COLLECTION_KEY = "collection"


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: SourceId
    kind: SourceKind
    enabled: bool = True
    max_items: int | None = Field(default=None, ge=1, le=200)
    """Overrides `max_items_per_source` for this source alone.

    One endpoint is one source here, where the retired `src/fetch.py` had one source per
    kind and split a shared budget across its subreddits and repositories.
    The override is how those per-endpoint shares are kept the same.
    """


class FeedSource(_Spec):
    """RSS 2.0 or Atom. The first choice whenever a source publishes a feed."""

    connector: Literal["feed"]
    url: HttpUrlStr
    window_days: int | None = Field(default=None, ge=1, le=90)
    """Overrides the collection's `window_days` for this feed alone.

    arXiv posts a survey on coding agents about once a month, which a week's window
    mostly misses. The processing state keeps a longer window from researching one twice.
    """
    categories: tuple[NonBlankStr, ...] | None = Field(default=None, min_length=1)
    """Keeps only the entries that carry at least one of these categories.

    Most of the fifteen posts a week in OpenAI's news feed are policy, partnerships
    and customer stories, which would take research slots from the other blogs. The
    feed's own categories tell the model and product posts apart. An entry with no
    category is left out, since the customer stories carry none. Case and spacing
    are ignored.
    """


class SitemapSource(_Spec):
    """A site with no feed but a sitemap declared in `robots.txt`.

    The sitemap gives a URL and a `lastmod`, never a title, so the connector
    reads the head metadata of the most recently modified candidates to fill
    the title in. `max_metadata_probes` caps how many extra requests that
    costs per run; the cap lives here so a run cannot widen it.
    """

    connector: Literal["sitemap"]
    url: HttpUrlStr
    url_prefix: HttpUrlStr
    """Only `<loc>` values starting with this prefix are candidates."""
    max_metadata_probes: int = Field(ge=0, le=50)


class BlogIndexSource(_Spec):
    """Blog listing in newest-first order, with bounded publication-date probes."""

    connector: Literal["blog_index"]
    url: HttpUrlStr
    url_prefix: HttpUrlStr
    link_class: NonBlankStr
    max_metadata_probes: int = Field(ge=1, le=50)


class HermesStoriesSource(_Spec):
    """Official story cards; dates belong to the linked original posts."""

    connector: Literal["hermes_stories"]
    url: HttpUrlStr


class HackerNewsSource(_Spec):
    connector: Literal["hackernews"]
    url: HttpUrlStr = "https://hn.algolia.com/api/v1/search"
    keywords: tuple[NonBlankStr, ...] = Field(min_length=1)
    min_points: int = Field(ge=0, default=30)
    window_hours: int = Field(ge=1, le=168, default=24)
    """Kept separate from `window_days`: the discovery feed is a daily cut."""
    hits_per_keyword: int = Field(ge=1, le=100, default=20)


class HuggingFacePapersSource(_Spec):
    connector: Literal["hf_papers"]
    url: HttpUrlStr = "https://huggingface.co/api/daily_papers"


class GitHubReleasesSource(_Spec):
    connector: Literal["gh_releases"]
    repo: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")]
    releases_per_page: int = Field(ge=1, le=100, default=5)


SourceSpec = Annotated[
    Union[
        FeedSource,
        HermesStoriesSource,
        BlogIndexSource,
        SitemapSource,
        HackerNewsSource,
        HuggingFacePapersSource,
        GitHubReleasesSource,
    ],
    Field(discriminator="connector"),
]

SOURCE_SPEC_ADAPTER: TypeAdapter[SourceSpec] = TypeAdapter(SourceSpec)


class HttpSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timeout_seconds: float = Field(gt=0, le=120)
    user_agent: NonBlankStr
    max_response_bytes: int = Field(ge=1024, le=64 * 1024 * 1024)


class CollectionConfig(BaseModel):
    """The `collection` block of `config.json`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    window_days: int = Field(ge=1, le=90)
    max_items_per_source: int = Field(ge=1, le=200)
    http: HttpSettings
    sources: tuple[SourceSpec, ...] = Field(min_length=1)

    @property
    def enabled_sources(self) -> tuple[SourceSpec, ...]:
        return tuple(source for source in self.sources if source.enabled)

    def items_for(self, spec: SourceSpec) -> int:
        return spec.max_items if spec.max_items is not None else self.max_items_per_source

    def window_days_for(self, spec: SourceSpec) -> int:
        if isinstance(spec, FeedSource) and spec.window_days is not None:
            return spec.window_days
        return self.window_days

    def source(self, source_id: str) -> SourceSpec:
        for spec in self.sources:
            if spec.id == source_id:
                return spec
        raise KeyError(source_id)

    @model_validator(mode="after")
    def _check_unique_ids(self) -> CollectionConfig:
        ids = [spec.id for spec in self.sources]
        if len(set(ids)) != len(ids):
            raise ValueError("source ids must be unique")
        return self


def load_collection_config(path: Path | str) -> CollectionConfig:
    """Read and validate the `collection` block of a config file.

    The caller names the file. A default derived from `__file__` only
    resolved inside the source tree, and pointed outside site-packages once
    the package shipped as a wheel, so the path comes from the caller: the
    pipeline passes the one in the repository it runs in.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if COLLECTION_KEY not in raw:
        raise KeyError(f"{path} has no {COLLECTION_KEY!r} block")
    return CollectionConfig.model_validate(raw[COLLECTION_KEY])


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
    # Source settings are defined here; load connector implementations after them.
    from .connectors import CONNECTORS, CollectContext, ProbeRecorder, dedupe

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
