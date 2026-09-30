"""Recorded responses and a fetcher that replays them.

Every fixture under `tests/fixtures/collect/` is a real response, trimmed to
a few entries and with article bodies cut out. The one exception is
`sitemap_index.xml`, a document shape no source serves today; its own comment
says so. No test here opens a socket:
`FixtureFetcher` answers from a map, and a URL it does not know raises, so a
connector that reaches for an unexpected address fails loudly.
"""

from __future__ import annotations

import datetime as dt
import json
import socket
import ssl
import urllib.error
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

from agent_daily_digest.collect.run import CollectionConfig
from agent_daily_digest.collect.transport import (
    HttpResponse,
    ResponseTooLargeError,
    UnsafeXmlError,
)
from agent_daily_digest.contracts.base import ErrorKind

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


# Exceptions are built on demand, never passed to `parametrize` as values:
# pytest 8.0 asks every parameter for `__name__`, and `HTTPError` answers that
# by reaching into a file object it does not have, which fails collection.
FAILURE_BUILDERS: dict[str, Callable[[], BaseException]] = {
    "http 429": lambda: http_error(429),
    "http 401": lambda: http_error(401),
    "http 403": lambda: http_error(403),
    "http 500": lambda: http_error(500),
    "http 404": lambda: http_error(404),
    "builtin timeout": lambda: TimeoutError("slow"),
    "socket timeout": lambda: socket.timeout("slow"),
    "url timeout": timeout,
    "url unreachable": lambda: urllib.error.URLError("no route"),
    "tls": lambda: ssl.SSLError("handshake"),
    "connection reset": lambda: ConnectionResetError("reset"),
    "xml parse": lambda: ET.ParseError("bad"),
    "xml entity": lambda: UnsafeXmlError("entity"),
    "json parse": lambda: json.JSONDecodeError("bad", "{", 0),
    "undecodable": lambda: UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
    "programming error": lambda: ValueError("something else"),
    "oversized response": lambda: ResponseTooLargeError("over 8 MB"),
}

FAILURE_KINDS: dict[str, ErrorKind] = {
    "http 429": ErrorKind.RATE_LIMIT,
    "http 401": ErrorKind.AUTHENTICATION,
    "http 403": ErrorKind.AUTHENTICATION,
    "http 500": ErrorKind.HTTP_STATUS,
    "http 404": ErrorKind.HTTP_STATUS,
    "builtin timeout": ErrorKind.TIMEOUT,
    "socket timeout": ErrorKind.TIMEOUT,
    "url timeout": ErrorKind.TIMEOUT,
    "url unreachable": ErrorKind.NETWORK,
    "tls": ErrorKind.NETWORK,
    "connection reset": ErrorKind.NETWORK,
    "xml parse": ErrorKind.PARSE,
    "xml entity": ErrorKind.PARSE,
    "json parse": ErrorKind.PARSE,
    "undecodable": ErrorKind.PARSE,
    "programming error": ErrorKind.UNEXPECTED,
    "oversized response": ErrorKind.UNEXPECTED,
}


def failure(name: str) -> BaseException:
    return FAILURE_BUILDERS[name]()


HTTP_SETTINGS = {
    "timeout_seconds": 20,
    "user_agent": "agent-daily-digest/test",
    "max_response_bytes": 1 << 20,
}


def config(
    *sources: dict,
    window_days: int = 7,
    max_items: int = 12,
    http: dict | None = None,
    **extra,
) -> CollectionConfig:
    return CollectionConfig.model_validate(
        {
            "window_days": window_days,
            "max_items_per_source": max_items,
            "http": HTTP_SETTINGS if http is None else http,
            "sources": list(sources),
            **extra,
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
