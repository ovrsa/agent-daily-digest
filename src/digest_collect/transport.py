"""The collection layer's only door to the network.

Connectors never call `urllib` themselves. They go through `Fetcher`, so a
test can hand them a recorded response instead of making a real request.

Responses are untrusted input (#1 "State and data handling"): the reader caps
how many bytes it keeps, and the XML parser refuses entity declarations.
"""

from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Protocol
from xml.parsers import expat

from digest_contracts import ErrorKind, ErrorRecord

DETAIL_MAX_CHARS = 200
"""Cap for `ErrorRecord.detail` written here.

Well under the contract's 500, because the detail exists to tell an operator
which door failed, not to carry a payload.
"""

DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class HttpResponse:
    """What a connector is allowed to see of an HTTP response."""

    url: str
    """The URL after redirects."""
    status: int
    body: bytes

    def text(self, *, limit: int | None = None) -> str:
        raw = self.body if limit is None else self.body[:limit]
        return raw.decode("utf-8", errors="replace")


class Fetcher(Protocol):
    def get(self, url: str, *, accept: str = "*/*") -> HttpResponse: ...


class ResponseTooLargeError(Exception):
    """The response exceeded the configured byte cap and was not read further."""


class UnsafeXmlError(Exception):
    """The document declared an XML entity. Feeds have no reason to."""


class UrllibFetcher:
    """Standard-library fetcher. No third-party HTTP client is needed."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        user_agent: str,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._user_agent = user_agent
        self._max_bytes = max_response_bytes
        self._ssl_context = ssl_context or ssl.create_default_context()

    def get(self, url: str, *, accept: str = "*/*") -> HttpResponse:
        request = urllib.request.Request(
            url, headers={"User-Agent": self._user_agent, "Accept": accept}
        )
        with urllib.request.urlopen(
            request, timeout=self._timeout, context=self._ssl_context
        ) as response:
            # One byte over the cap is enough to tell the difference between a
            # response that fits and one that was truncated.
            body = response.read(self._max_bytes + 1)
            if len(body) > self._max_bytes:
                raise ResponseTooLargeError(f"over {self._max_bytes} bytes")
            return HttpResponse(url=response.geturl(), status=response.status, body=body)


def parse_xml(data: bytes) -> ET.Element:
    """Parse untrusted XML with entity declarations refused.

    `xml.etree.ElementTree.XMLParser` expands internal entities, so a feed
    could blow up memory with nested declarations, and its C implementation
    exposes no way to turn that off. Driving expat directly does: pyexpat
    never fetches external entities on its own, so refusing declarations
    closes the remaining hole without a third-party parser.

    Undefined entity references still raise `ET.ParseError`, which is what
    `XMLParser` does too, so a feed that used to parse still parses.
    """
    builder = ET.TreeBuilder()
    parser = expat.ParserCreate(namespace_separator="}")

    def _reject(*_args: object, **_kwargs: object) -> None:
        raise UnsafeXmlError("XML entity declarations are not accepted")

    parser.buffer_text = True
    parser.EntityDeclHandler = _reject
    parser.ExternalEntityRefHandler = _reject
    parser.StartElementHandler = lambda tag, attrs: builder.start(
        _qualified(tag), {_qualified(key): value for key, value in attrs.items()}
    )
    parser.EndElementHandler = lambda tag: builder.end(_qualified(tag))
    parser.CharacterDataHandler = builder.data
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise ET.ParseError(str(exc)) from exc
    return builder.close()


def _qualified(name: str) -> str:
    """Rewrite expat's `uri}local` into ElementTree's `{uri}local`."""
    return "{" + name if "}" in name else name


def parse_json(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"))


_TIMEOUTS = (TimeoutError, socket.timeout)


def classify_failure(exc: BaseException) -> ErrorRecord:
    """Turn a connector exception into a record that is safe to keep.

    The detail is built from the exception type and its message. None of the
    exceptions classified here carry response bodies in their message, and the
    length cap limits the damage if a connector ever raises one that does.
    """
    return ErrorRecord(kind=_classify_kind(exc), detail=_detail(exc))


def _classify_kind(exc: BaseException) -> ErrorKind:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 429:
            return ErrorKind.RATE_LIMIT
        if exc.code in (401, 403):
            return ErrorKind.AUTHENTICATION
        return ErrorKind.HTTP_STATUS
    if isinstance(exc, _TIMEOUTS):
        return ErrorKind.TIMEOUT
    if isinstance(exc, urllib.error.URLError):
        return ErrorKind.TIMEOUT if isinstance(exc.reason, _TIMEOUTS) else ErrorKind.NETWORK
    if isinstance(exc, (ssl.SSLError, ConnectionError, OSError)):
        return ErrorKind.NETWORK
    if isinstance(
        exc,
        (
            ET.ParseError,
            expat.ExpatError,
            UnsafeXmlError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ),
    ):
        return ErrorKind.PARSE
    return ErrorKind.UNEXPECTED


def _detail(exc: BaseException) -> str | None:
    if isinstance(exc, urllib.error.HTTPError):
        text = f"HTTP {exc.code}"
    else:
        message = str(exc).strip()
        text = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    text = " ".join(text.split())[:DETAIL_MAX_CHARS]
    return text or None
