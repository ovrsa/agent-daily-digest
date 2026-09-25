"""Body fetching, normalization, deterministic gates and processing state.

Import from this package, not from its submodules. The contracts this layer
fills come from `agent_daily_digest.contracts`; nothing here defines a contract of its own.

The pieces, in the order a run uses them:

- `canonicalize_url`, `parse_published_at` decide `required_fields`
- `fetch_page` (or any `BodyFetcher`) and `extract_document` produce a body
- `normalize_item` / `normalize_items` run the gates and build the article
- `as_untrusted_block` is the boundary that hands an article to a model
- `load_state`, `record_article`, `save_state` keep the four persisted fields
"""

from .boundary import HEADER, UNTRUSTED_CLOSE, UNTRUSTED_OPEN, as_untrusted_block, escape_untrusted
from ._text import INVISIBLE, collapse_text, is_blank, normalize_block, strip_invisible
from .dates import PublishedAtProblem, PublishedAtRejected, parse_published_at
from .extract import MAX_AUTHOR_CHARS, ExtractedDocument, extract_document
from .fetching import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_BODY_BYTES,
    MAX_REDIRECTS,
    USER_AGENT,
    BlockedTarget,
    BodyFetcher,
    FetchedPage,
    FetchFailure,
    FetchOutcome,
    decode_html,
    fetch_page,
    is_html_content_type,
    is_public_address,
)
from .pipeline import (
    MIN_PRIMARY_INFO_CHARS,
    NormalizationResult,
    RunSeen,
    normalize_item,
    normalize_items,
)
from .state import (
    DEFAULT_STATE_PATH,
    ProcessedIndex,
    dump_state_json,
    load_state,
    record_article,
    save_state,
)
from .urls import (
    MAX_URL_CHARS,
    TRACKING_PARAMETERS,
    UrlProblem,
    UrlRejected,
    canonicalize_url,
    same_site,
)

__all__ = [
    "DEFAULT_STATE_PATH",
    "DEFAULT_TIMEOUT_SECONDS",
    "HEADER",
    "INVISIBLE",
    "MAX_AUTHOR_CHARS",
    "MAX_BODY_BYTES",
    "MAX_REDIRECTS",
    "MAX_URL_CHARS",
    "MIN_PRIMARY_INFO_CHARS",
    "TRACKING_PARAMETERS",
    "UNTRUSTED_CLOSE",
    "UNTRUSTED_OPEN",
    "USER_AGENT",
    "BlockedTarget",
    "BodyFetcher",
    "ExtractedDocument",
    "FetchFailure",
    "FetchOutcome",
    "FetchedPage",
    "NormalizationResult",
    "ProcessedIndex",
    "PublishedAtProblem",
    "PublishedAtRejected",
    "RunSeen",
    "UrlProblem",
    "UrlRejected",
    "as_untrusted_block",
    "canonicalize_url",
    "collapse_text",
    "decode_html",
    "dump_state_json",
    "escape_untrusted",
    "extract_document",
    "fetch_page",
    "is_blank",
    "is_html_content_type",
    "is_public_address",
    "load_state",
    "normalize_block",
    "normalize_item",
    "normalize_items",
    "parse_published_at",
    "record_article",
    "same_site",
    "save_state",
    "strip_invisible",
]
