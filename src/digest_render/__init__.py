"""Deterministic Markdown renderer for the daily digest.

Import from this package, not from its submodules. Everything listed in
`__all__` is the public API the pipeline (#10) builds on.

The renderer turns a `SelectorOutput` plus the `NormalizedArticle` metadata it
references into `digests/<date>.md` and the index in `digests/README.md`. It
calls no model and writes no wording of its own: the sentences come from
`DigestEntry` and the running order comes from `SelectorOutput`, so the same
input always produces the same bytes.
"""

from .digest import (
    DIGEST_TITLE,
    DIGEST_TYPE,
    digest_filename,
    forbidden_artifacts_in,
    render_digest,
)
from .errors import (
    ForbiddenArtifactError,
    IndexMarkerError,
    MissingArticleError,
    RenderError,
    TierCapError,
)
from .forbidden import (
    MATCHED_MAX_CHARS,
    RULE_DESCRIPTIONS,
    ForbiddenArtifact,
    ForbiddenRule,
    check_forbidden_artifacts,
)
from .index import (
    INDEX_BEGIN,
    INDEX_EMPTY_TEXT,
    INDEX_END,
    INDEX_MAX_ENTRIES,
    README_FILENAME,
    list_digest_dates,
    render_index,
    update_index,
)
from .markdown import inline, link
from .publish import write_digest

__all__ = [
    "DIGEST_TITLE",
    "DIGEST_TYPE",
    "INDEX_BEGIN",
    "INDEX_EMPTY_TEXT",
    "INDEX_END",
    "INDEX_MAX_ENTRIES",
    "MATCHED_MAX_CHARS",
    "README_FILENAME",
    "RULE_DESCRIPTIONS",
    "ForbiddenArtifact",
    "ForbiddenArtifactError",
    "ForbiddenRule",
    "IndexMarkerError",
    "MissingArticleError",
    "RenderError",
    "TierCapError",
    "check_forbidden_artifacts",
    "digest_filename",
    "forbidden_artifacts_in",
    "inline",
    "link",
    "list_digest_dates",
    "render_digest",
    "render_index",
    "update_index",
    "write_digest",
]
