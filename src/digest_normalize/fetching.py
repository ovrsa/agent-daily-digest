"""Fetching the original page.

The pipeline takes a `BodyFetcher` rather than calling the network itself, so
gate behaviour can be tested without a connection and #10 can wrap the call in
its own timing and retry policy. `fetch_page` is the ordinary implementation:
standard library only, with a byte cap, a content-type check and no redirect to
a scheme other than http(s).
"""

from __future__ import annotations

import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from collections.abc import Callable
from email.message import Message

from digest_contracts import ERROR_DETAIL_MAX_CHARS, ErrorKind, ErrorRecord

MAX_BODY_BYTES = 2 * 1024 * 1024
"""Bytes read from one page before the fetch is abandoned."""

DEFAULT_TIMEOUT_SECONDS = 20.0

USER_AGENT = "agent-daily-digest/0.1 (+https://github.com/ovrsa/agent-daily-digest)"

_HTML_TYPES = ("text/html", "application/xhtml+xml", "application/xml", "text/xml", "text/plain")

_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:+-]+)""", re.IGNORECASE)
_CHARSET_SNIFF_BYTES = 4096


@dataclass(frozen=True)
class FetchedPage:
    """A page as it came back, before any extraction."""

    requested_url: str
    final_url: str
    html: str
    content_type: str | None = None


@dataclass(frozen=True)
class FetchFailure:
    """Why a page could not be read, in a shape that is safe to record."""

    kind: ErrorKind
    detail: str | None = None

    def to_error_record(self) -> ErrorRecord:
        detail = (self.detail or "").strip()[:ERROR_DETAIL_MAX_CHARS]
        return ErrorRecord(kind=self.kind, detail=detail or None)


FetchOutcome = FetchedPage | FetchFailure
BodyFetcher = Callable[[str], FetchOutcome]


def is_html_content_type(value: str | None) -> bool:
    """True when the response is text this layer can extract from."""
    if not value:
        return False
    return value.split(";", 1)[0].strip().lower() in _HTML_TYPES


def decode_html(raw: bytes, content_type: str | None) -> str:
    """Decode page bytes deterministically: header charset, then meta, then UTF-8."""
    for charset in (_charset_of(content_type), _meta_charset(raw), "utf-8"):
        if not charset:
            continue
        try:
            return raw.decode(charset, errors="replace").lstrip("\ufeff")
        except LookupError:
            continue
    return raw.decode("utf-8", errors="replace").lstrip("\ufeff")


def fetch_page(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = MAX_BODY_BYTES,
) -> FetchOutcome:
    """Read one page over http(s). Every failure is returned, never raised."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - scheme checked
            content_type = response.headers.get("Content-Type")
            if not is_html_content_type(content_type):
                return FetchFailure(kind=ErrorKind.PARSE, detail=f"unsupported content type: {content_type}")
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                return FetchFailure(kind=ErrorKind.PARSE, detail=f"body over {max_bytes} bytes")
            final_url = response.geturl()
    except urllib.error.HTTPError as error:
        return FetchFailure(kind=ErrorKind.HTTP_STATUS, detail=f"HTTP {error.code}")
    except socket.timeout:
        return FetchFailure(kind=ErrorKind.TIMEOUT, detail=f"no response within {timeout}s")
    except urllib.error.URLError as error:
        reason = error.reason
        if isinstance(reason, socket.timeout):
            return FetchFailure(kind=ErrorKind.TIMEOUT, detail=f"no response within {timeout}s")
        return FetchFailure(kind=ErrorKind.NETWORK, detail=str(reason))
    except (ValueError, OSError) as error:
        return FetchFailure(kind=ErrorKind.NETWORK, detail=str(error))

    return FetchedPage(
        requested_url=url,
        final_url=final_url or url,
        html=decode_html(raw, content_type),
        content_type=content_type,
    )


def _charset_of(content_type: str | None) -> str | None:
    if not content_type:
        return None
    message = Message()
    message["Content-Type"] = content_type
    return message.get_param("charset")  # type: ignore[return-value]


def _meta_charset(raw: bytes) -> str | None:
    match = _META_CHARSET.search(raw[:_CHARSET_SNIFF_BYTES])
    return match.group(1).decode("ascii", errors="replace") if match else None
