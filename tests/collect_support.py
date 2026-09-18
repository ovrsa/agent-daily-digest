"""Recorded responses and a fetcher that replays them.

Every fixture under `tests/fixtures/collect/` is a real response, trimmed to
a few entries and with article bodies cut out. No test here opens a socket:
`FixtureFetcher` answers from a map, and a URL it does not know raises, so a
connector that reaches for an unexpected address fails loudly.
"""

from __future__ import annotations

import datetime as dt
import urllib.error
from pathlib import Path

from digest_collect import CollectionConfig, HttpResponse

FIXTURES = Path(__file__).parent / "fixtures" / "collect"

NOW = dt.datetime(2026, 9, 18, 6, 0, tzinfo=dt.timezone.utc)
"""Fixed clock. The fixtures were recorded on 2026-09-17 and 2026-09-18."""

CLAUDE_SITEMAP = "https://claude.com/sitemap.xml"
ANTHROPIC_SITEMAP = "https://www.anthropic.com/sitemap.xml"


def read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FixtureFetcher:
    """Replays recorded responses, and records what was asked for."""

    def __init__(self, routes: dict[str, object], *, default: object | None = None) -> None:
        self._routes = routes
        self._default = default
        self.requested: list[str] = []

    def get(self, url: str, *, accept: str = "*/*") -> HttpResponse:
        self.requested.append(url)
        answer = self._routes.get(url, self._default)
        if answer is None:
            raise AssertionError(f"no recorded response for {url}")
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, HttpResponse):
            return answer
        if callable(answer):
            return answer(url)
        return HttpResponse(url=url, status=200, body=answer)


def http_error(code: int, url: str = "https://example.com/x") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "recorded", {}, None)  # type: ignore[arg-type]


def timeout() -> urllib.error.URLError:
    return urllib.error.URLError(TimeoutError("timed out"))


def config(*sources: dict, window_days: int = 7, max_items: int = 12) -> CollectionConfig:
    return CollectionConfig.model_validate(
        {
            "window_days": window_days,
            "max_items_per_source": max_items,
            "http": {
                "timeout_seconds": 20,
                "user_agent": "agent-daily-digest/test",
                "max_response_bytes": 1 << 20,
            },
            "sources": list(sources),
        }
    )


def feed_source(source_id: str = "simonw", *, url: str = "https://example.com/feed", **extra):
    return {
        "id": source_id,
        "kind": "fixed_watch",
        "connector": "feed",
        "enabled": True,
        "url": url,
        **extra,
    }


def sitemap_source(
    source_id: str = "claude_blog",
    *,
    url: str = CLAUDE_SITEMAP,
    url_prefix: str = "https://claude.com/blog/",
    max_metadata_probes: int = 10,
    **extra,
):
    return {
        "id": source_id,
        "kind": "fixed_watch",
        "connector": "sitemap",
        "enabled": True,
        "url": url,
        "url_prefix": url_prefix,
        "max_metadata_probes": max_metadata_probes,
        **extra,
    }
