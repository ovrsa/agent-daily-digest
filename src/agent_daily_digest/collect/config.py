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
