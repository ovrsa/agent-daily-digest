"""Source collection for the digest pipeline.

`collect_all` runs every enabled source in `config/config.json`'s
`collection` block and returns a `CollectionReport`: one
`digest_contracts.SourceFetchResult` per source, plus the probe counts this
layer owns. Article bodies are not fetched here; that is #5.

`src/fetch.py` is the collector the routine runs today and is untouched. #11
retires it.
"""

from .collector import CollectionReport, SourceProbeStats, collect_all, collect_source
from .config import (
    COLLECTION_KEY,
    CollectionConfig,
    FeedSource,
    GitHubReleasesSource,
    HackerNewsSource,
    HttpSettings,
    HuggingFacePapersSource,
    SitemapSource,
    SourceSpec,
    load_collection_config,
)
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
