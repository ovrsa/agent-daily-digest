"""Source collection for the digest pipeline.

`collect_all` runs every enabled source in `config.json`'s
`collection` block and returns a `CollectionReport`: one
`agent_daily_digest.contracts.SourceFetchResult` per source, plus the probe counts this
layer owns. Article bodies are not fetched here; that is #5.
"""

from agent_daily_digest.collect.run import CollectionReport, SourceProbeStats, collect_all, collect_source
from agent_daily_digest.collect.run import COLLECTION_KEY, BlogIndexSource, CollectionConfig, FeedSource, GitHubReleasesSource, HackerNewsSource, HermesStoriesSource, HttpSettings, HuggingFacePapersSource, SitemapSource, SourceSpec, load_collection_config
from .connectors import (
    CONNECTORS,
    CollectContext,
    PageMetadata,
    ProbeRecorder,
    make_article_id,
    read_head_metadata,
)
from .transport import (
    DEFAULT_MAX_RESPONSE_BYTES,
    DETAIL_MAX_CHARS,
    Fetcher,
    HttpResponse,
    ResponseTooLargeError,
    SitemapIndexError,
    UnsafeXmlError,
    UrllibFetcher,
    classify_failure,
    make_fetcher,
    parse_json,
    parse_xml,
)

__all__ = [
    "CONNECTORS",
    "COLLECTION_KEY",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DETAIL_MAX_CHARS",
    "CollectContext",
    "CollectionConfig",
    "CollectionReport",
    "Fetcher",
    "FeedSource",
    "BlogIndexSource",
    "HermesStoriesSource",
    "GitHubReleasesSource",
    "HackerNewsSource",
    "HttpResponse",
    "HttpSettings",
    "HuggingFacePapersSource",
    "PageMetadata",
    "ProbeRecorder",
    "ResponseTooLargeError",
    "SitemapIndexError",
    "SitemapSource",
    "SourceProbeStats",
    "SourceSpec",
    "UnsafeXmlError",
    "UrllibFetcher",
    "classify_failure",
    "collect_all",
    "collect_source",
    "load_collection_config",
    "make_article_id",
    "make_fetcher",
    "parse_json",
    "parse_xml",
    "read_head_metadata",
]
