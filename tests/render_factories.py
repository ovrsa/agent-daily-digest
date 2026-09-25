"""Inputs for the renderer tests.

The Selector side is a JSON fixture, mirroring what the model returns. The
article metadata is built here because `NormalizedArticle.content_hash` has to
match its body, which a hand-written JSON file cannot keep true.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from agent_daily_digest.contracts import NormalizedArticle, SelectorOutput, compute_content_hash
from agent_daily_digest.render import render_digest

DIGEST_DATE = date(2026, 9, 18)
"""The date the snapshot was rendered for."""

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "render"
SELECTOR_OUTPUT_PATH = FIXTURE_DIR / "selector_output.json"
DIGEST_SNAPSHOT_PATH = FIXTURE_DIR / "digest.md"

BODY = "Synthetic normalized body used only by renderer tests."

# Titles and URLs are quoted source text, so the set covers what a real feed
# produces: Markdown link syntax in a title, and parentheses in a URL.
_ARTICLE_METADATA: tuple[dict[str, Any], ...] = (
    {
        "article_id": "a001",
        "source_id": "anthropic_engineering",
        "source_kind": "fixed_watch",
        "canonical_url": "https://example.com/posts/plan-act-split",
        "title": "Splitting plan and act into separate agent sessions",
        "author": "Alice Kim",
        "published_at": "2026-09-17T09:30:00+09:00",
    },
    {
        "article_id": "a002",
        "source_id": "github_releases",
        "source_kind": "fixed_watch",
        "canonical_url": "https://example.com/releases/tool-scopes-v0-9",
        "title": "Per-tool permission scopes [v0.9]",
        "author": None,
        "published_at": "2026-09-16T22:10:00+00:00",
    },
    {
        "article_id": "a003",
        "source_id": "simonw",
        "source_kind": "fixed_watch",
        "canonical_url": "https://example.com/posts/retry-cost",
        "title": "Measuring retry cost in a long agent loop",
        "author": "Simon Willison",
        "published_at": "2026-09-17T01:05:00+00:00",
    },
    {
        "article_id": "a004",
        "source_id": "latent_space",
        "source_kind": "discovery",
        "canonical_url": "https://example.com/posts/cache-hit-rates",
        "title": "Prompt cache hit rates in production",
        "author": None,
        "published_at": "2026-09-15T12:00:00-07:00",
    },
    {
        "article_id": "a005",
        "source_id": "github_releases",
        "source_kind": "fixed_watch",
        "canonical_url": "https://example.com/releases/(2026-09-16)",
        "title": "Release notes (2026-09-16)",
        "author": "Paul Gauthier",
        "published_at": "2026-09-16T18:45:00+00:00",
    },
)


def normalized_article(**overrides: Any) -> NormalizedArticle:
    data: dict[str, Any] = {
        **_ARTICLE_METADATA[0],
        "body_source": "extracted",
        "body_text": BODY,
        "content_hash": compute_content_hash(BODY),
    }
    data.update(overrides)
    return NormalizedArticle.model_validate(data)


def articles() -> dict[str, NormalizedArticle]:
    """Metadata for every article the fixture Selector output references."""
    return {
        meta["article_id"]: normalized_article(**meta) for meta in _ARTICLE_METADATA
    }


def selector_payload() -> dict[str, Any]:
    return json.loads(SELECTOR_OUTPUT_PATH.read_text(encoding="utf-8"))


def selector_output(**overrides: Any) -> SelectorOutput:
    data = selector_payload()
    data.update(overrides)
    return SelectorOutput.model_validate(data)


def included_payload(article_id: str = "a001", **entry_overrides: Any) -> dict[str, Any]:
    """One `IncludedArticle` payload, for tests that vary a single entry."""
    data = selector_payload()["must_read"][0]
    data["article_id"] = article_id
    data["entry"].update(entry_overrides)
    return data


def empty_selector_output() -> SelectorOutput:
    """A valid run that adopted nothing. Excluding articles is still a result."""
    return SelectorOutput.model_validate(
        {
            "must_read": [],
            "worth_knowing": [],
            "excluded": selector_payload()["excluded"],
            "duplicate_groups": [],
        }
    )


README_TEMPLATE = (
    "# Digests\n"
    "\n"
    "アーカイブ。\n"
    "\n"
    "<!-- INDEX:START -->\n"
    "_（まだダイジェストはありません。最初の実行で生成されます。）_\n"
    "<!-- INDEX:END -->\n"
)


# Aliased so `render` can keep the natural keyword names without shadowing.
_default_selector_output = selector_output
_default_articles = articles


def render(
    selector_output: SelectorOutput | None = None,
    articles: Mapping[str, NormalizedArticle] | None = None,
    digest_date: date = DIGEST_DATE,
) -> str | None:
    """`render_digest` with the fixture defaults, so a test varies one thing."""
    return render_digest(
        _default_selector_output() if selector_output is None else selector_output,
        _default_articles() if articles is None else articles,
        digest_date,
    )
