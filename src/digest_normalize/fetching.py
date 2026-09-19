"""Fetching the original page.

The pipeline takes a `BodyFetcher` rather than calling the network itself, so
gate behaviour can be tested without a connection and #10 can wrap the call in
its own timing and retry policy. `fetch_page` is the ordinary implementation:
standard library only, with a byte cap, a content-type check, and a refusal to
open or follow a redirect to any address that is not publicly routable.

That last check is what stops a feed from using the digest as a proxy into the
network the scheduled job runs in: an item whose URL resolves to loopback, a
private range or the link-local metadata address would otherwise be fetched and
its response carried into the Selector prompt and the published digest.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from collections.abc import Callable
from email.message import Message

from digest_contracts import ERROR_DETAIL_MAX_CHARS, ErrorKind, ErrorRecord

MAX_BODY_BYTES = 2 * 1024 * 1024
"""Bytes read from one page before the fetch is abandoned."""

MAX_REDIRECTS = 5
"""Redirects followed before the fetch is abandoned. Every hop is re-checked."""

DEFAULT_TIMEOUT_SECONDS = 20.0

USER_AGENT = "agent-daily-digest/0.1 (+https://github.com/ovrsa/agent-daily-digest)"

DEFAULT_SCHEMES = ("http", "https")

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

Resolver = Callable[..., list]
"""`socket.getaddrinfo`, or a stand-in so tests can resolve without a network."""


class BlockedTarget(Exception):
    """A URL that does not resolve to a publicly routable address."""


BLOCKED_IPV4_NETWORKS = tuple(
    ipaddress.IPv4Network(cidr)
    for cidr in (
        "0.0.0.0/8",  # this host on this network
        "10.0.0.0/8",  # private use
        "100.64.0.0/10",  # shared address space, carrier NAT
        "127.0.0.0/8",  # loopback
        "169.254.0.0/16",  # link local, and the cloud metadata address
        "172.16.0.0/12",  # private use
        "192.0.0.0/24",  # IETF protocol assignments
        "192.0.2.0/24",  # documentation, TEST-NET-1
        "192.88.99.0/24",  # 6to4 relay anycast, deprecated by RFC 7526
        "192.168.0.0/16",  # private use
        "198.18.0.0/15",  # benchmarking
        "198.51.100.0/24",  # documentation, TEST-NET-2
        "203.0.113.0/24",  # documentation, TEST-NET-3
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved, and 255.255.255.255 limited broadcast
    )
)
"""IPv4 ranges this layer refuses, from the IANA special-purpose registry."""

BLOCKED_IPV6_NETWORKS = tuple(
    ipaddress.IPv6Network(cidr)
    for cidr in (
        "::/96",  # unspecified, loopback, and the deprecated IPv4-compatible form
        "64:ff9b:1::/48",  # local-use IPv4/IPv6 translation
        "100::/64",  # discard only
        "2001::/23",  # IETF protocol assignments: Teredo, benchmarking, ORCHIDv2
        "2001:db8::/32",  # documentation
        "2002::/16",  # 6to4, deprecated by RFC 7526
        "3fff::/20",  # documentation, RFC 9637
        "5f00::/16",  # segment routing SIDs
        "fc00::/7",  # unique local
        "fe80::/10",  # link local
        "ff00::/8",  # multicast
    )
)
"""IPv6 ranges this layer refuses, from the IANA special-purpose registry."""

TRANSLATED_IPV6_NETWORKS = tuple(
    ipaddress.IPv6Network(cidr)
    for cidr in (
        "::ffff:0:0/96",  # IPv4-mapped
        "64:ff9b::/96",  # the well-known NAT64 prefix, RFC 6052
    )
)
"""Ranges that carry an IPv4 address in their last 32 bits.

These are judged by the address they wrap rather than refused outright, so a
NAT64 network can still reach a public site while `64:ff9b::7f00:1` - which is
127.0.0.1 - is refused. `64:ff9b:1::/48` is not here because RFC 6052 allows the
IPv4 address at several offsets inside it; it is refused as a whole instead.
"""


def _is_blocked_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True when `address` falls in a range this layer refuses to open.

    The ranges come from the tables above rather than from `ipaddress.is_global`,
    because that property's contents changed between 3.10 and 3.11 and
    `requires-python` allows both. Delegating would make the same feed item
    fetched on one interpreter and refused on another - the dependence that
    `dates.py` writes its own month table and ISO parser to avoid.
    """
    if address.version == 4:
        return any(address in network for network in BLOCKED_IPV4_NETWORKS)
    for network in TRANSLATED_IPV6_NETWORKS:
        if address in network:
            wrapped = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
            return _is_blocked_address(wrapped)
    return any(address in network for network in BLOCKED_IPV6_NETWORKS)


def is_public_address(host: str, *, resolve: Resolver = socket.getaddrinfo) -> bool:
    """True when every address `host` resolves to is publicly routable.

    A host that does not resolve, resolves to nothing, or resolves to anything
    loopback, private, link-local, multicast or otherwise reserved is refused.
    One non-public address is enough to refuse the host, so a name that answers
    with both a public and a private address does not get through.

    What counts as reserved is the tables above, so the same host is accepted or
    refused the same way on every supported interpreter.
    """
    try:
        infos = resolve(host, None)
    except (socket.gaierror, UnicodeError, ValueError):
        return False
    addresses = [info[4][0] for info in infos]
    if not addresses:
        return False
    for raw in addresses:
        try:
            # A scoped IPv6 address arrives as `fe80::1%en0`.
            address = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError:
            return False
        if _is_blocked_address(address):
            return False
    return True


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Applies the scheme and address checks again at every redirect hop."""

    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        _check_target(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_target(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in DEFAULT_SCHEMES:
        raise BlockedTarget(f"scheme {parts.scheme!r}")
    host = parts.hostname
    if not host or not is_public_address(host):
        raise BlockedTarget("host does not resolve to a public address")


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
    opener = urllib.request.build_opener(_GuardedRedirectHandler)
    try:
        _check_target(url)
        with opener.open(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type")
            if not is_html_content_type(content_type):
                return FetchFailure(kind=ErrorKind.PARSE, detail=f"unsupported content type: {content_type}")
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                return FetchFailure(kind=ErrorKind.PARSE, detail=f"body over {max_bytes} bytes")
            final_url = response.geturl()
    except BlockedTarget as blocked:
        return FetchFailure(kind=ErrorKind.NETWORK, detail=f"blocked target: {blocked}")
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
