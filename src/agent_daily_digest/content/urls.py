"""Canonical URL derivation.

The result is the identity of an article: it is written to the Git-tracked
processing state and it is what the duplicate gates compare. Two spellings of
the same page must reduce to the same string, and a string that reaches the
state must already satisfy `HttpUrlStr`.

Query parameters keep their original order. Sorting them would produce a tidier
key but a different page on servers that read a parameter sequence, and the
canonical URL is also the link the digest publishes.
"""

from __future__ import annotations

import ipaddress
import re
from enum import Enum
from urllib.parse import quote, urlsplit, urlunsplit
from agent_daily_digest.content.text import is_blank, strip_invisible


MAX_URL_CHARS = 2048
"""Same cap as `HttpUrlStr`, checked here so the gate can report `invalid_url`."""

DEFAULT_PORTS = {"http": 80, "https": 443}

TRACKING_PARAMETERS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "utm_name",
        "utm_brand",
        "utm_reader",
        "gclid",
        "dclid",
        "fbclid",
        "msclkid",
        "yclid",
        "twclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "ref_src",
        "ref_url",
        "_hsenc",
        "_hsmi",
    }
)
"""Parameters that identify a referral, never a document. Matched case-insensitively."""

_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_CONTROL = re.compile(r"[\x00-\x20\x7f]")


class UrlProblem(Enum):
    """Why a URL is unusable, in the two shapes the gate records."""

    MISSING = "missing"
    INVALID = "invalid"


class UrlRejected(ValueError):
    def __init__(self, problem: UrlProblem) -> None:
        super().__init__(problem.value)
        self.problem = problem


def canonicalize_url(raw: str | None) -> str:
    """Return the canonical form of `raw`, or raise `UrlRejected`."""
    if is_blank(raw):
        raise UrlRejected(UrlProblem.MISSING)
    assert raw is not None
    value = strip_invisible(raw).strip()
    if _CONTROL.search(value):
        raise UrlRejected(UrlProblem.INVALID)

    try:
        parts = urlsplit(value)
    except ValueError:  # 3.12 and later reject a bracketed host that is not an address
        raise UrlRejected(UrlProblem.INVALID) from None
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise UrlRejected(UrlProblem.INVALID)
    try:
        host, port = parts.hostname, parts.port
    except ValueError:  # a port that is not a number
        raise UrlRejected(UrlProblem.INVALID) from None
    if not host or parts.username is not None or parts.password is not None:
        raise UrlRejected(UrlProblem.INVALID)

    host = host.rstrip(".")
    if not host:
        raise UrlRejected(UrlProblem.INVALID)
    if "[" in parts.netloc or ":" in host:
        host = _canonical_ipv6_host(host)
    netloc = host if port in (None, DEFAULT_PORTS[scheme]) else f"{host}:{port}"

    canonical = urlunsplit((scheme, netloc, _canonical_path(parts.path), _canonical_query(parts.query), ""))
    if len(canonical) > MAX_URL_CHARS:
        raise UrlRejected(UrlProblem.INVALID)
    return canonical


def _canonical_ipv6_host(host: str) -> str:
    """Return an IPv6 host as one bracketed spelling, or reject it.

    `urlsplit` hands back the address without the brackets a URL needs, so
    writing it straight into the netloc produces a string no client resolves -
    and that string would become the article's identity in the state file and
    the link the digest publishes. An address also has several spellings, so it
    is compressed here: two feeds writing the same host must reach one identity.

    Rejecting what is not an address keeps the answer the same on every
    interpreter. `urlsplit` itself only started refusing `[not-an-address]` in
    3.12, and brackets are for an IPv6 literal alone, so `[8.8.8.8]` is refused
    on both ends rather than on one.
    """
    try:
        address = ipaddress.IPv6Address(host)
    except ValueError:
        raise UrlRejected(UrlProblem.INVALID) from None
    if address.scope_id is not None:  # `fe80::1%en0` names an interface, not a site
        raise UrlRejected(UrlProblem.INVALID)
    if address.ipv4_mapped is not None:
        # Python versions differ on whether compressed mapped addresses use
        # dotted IPv4 notation. Keep the URL identity stable across runtimes.
        high = int.from_bytes(address.packed[-4:-2], "big")
        low = int.from_bytes(address.packed[-2:], "big")
        return f"[::ffff:{high:x}:{low:x}]"
    return f"[{address.compressed}]"


def same_site(one: str, other: str) -> bool:
    """True when two canonical URLs have the same scheme, host and port."""
    left, right = urlsplit(one), urlsplit(other)
    return (left.scheme, left.netloc) == (right.scheme, right.netloc)


def _canonical_path(path: str) -> str:
    if not path:
        return "/"
    return _normalize_escapes(_remove_dot_segments(path))


def _remove_dot_segments(path: str) -> str:
    # RFC 3986 section 5.2.4, kept as a plain loop so the result is obvious.
    out: list[str] = []
    for segment in path.split("/")[1:] if path.startswith("/") else path.split("/"):
        if segment == ".":
            continue
        if segment == "..":
            if out:
                out.pop()
            continue
        out.append(segment)
    return "/" + "/".join(out)


def _normalize_escapes(value: str) -> str:
    # Decode the escapes RFC 3986 calls unreserved and uppercase the hex digits
    # of the rest, so a percent-encoded delimiter never becomes a real one.
    return quote(_ESCAPE.sub(_decode_unreserved, value), safe="/%:@!$&'()*+,;=~-._")


def _decode_unreserved(match: re.Match[str]) -> str:
    character = chr(int(match.group(1), 16))
    if character.isascii() and (character.isalnum() or character in "-._~"):
        return character
    return "%" + match.group(1).upper()


def _canonical_query(query: str) -> str:
    if not query:
        return ""
    kept = [
        pair
        for pair in query.split("&")
        if pair and pair.split("=", 1)[0].lower() not in TRACKING_PARAMETERS
    ]
    return "&".join(kept)
