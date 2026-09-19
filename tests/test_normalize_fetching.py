"""Body fetching helpers. No test in this file opens a network connection."""

from __future__ import annotations

import ipaddress
import socket
import urllib.request

import pytest

from digest_contracts import ErrorKind
from digest_normalize import (
    MAX_BODY_BYTES,
    MAX_REDIRECTS,
    BlockedTarget,
    FetchFailure,
    FetchedPage,
    decode_html,
    fetch_page,
    is_html_content_type,
    is_public_address,
)
from digest_normalize.fetching import (
    BLOCKED_IPV4_NETWORKS,
    BLOCKED_IPV6_NETWORKS,
    TRANSLATED_IPV6_NETWORKS,
    _GuardedRedirectHandler,
)


class TestContentType:
    @pytest.mark.parametrize(
        "value",
        [
            "text/html",
            "text/html; charset=utf-8",
            "TEXT/HTML;charset=UTF-8",
            "application/xhtml+xml",
            "text/plain",
        ],
    )
    def test_accepted(self, value: str) -> None:
        assert is_html_content_type(value)

    @pytest.mark.parametrize(
        "value",
        [None, "", "application/pdf", "image/png", "application/octet-stream", "video/mp4"],
    )
    def test_rejected(self, value: str | None) -> None:
        assert not is_html_content_type(value)


class TestDecodeHtml:
    def test_charset_from_the_header_wins(self) -> None:
        raw = "<html><body><p>café</p></body></html>".encode("iso-8859-1")
        assert "café" in decode_html(raw, "text/html; charset=iso-8859-1")

    def test_meta_charset_is_used_without_a_header_charset(self) -> None:
        raw = '<html><head><meta charset="iso-8859-1"></head><body><p>café</p></body></html>'.encode(
            "iso-8859-1"
        )
        assert "café" in decode_html(raw, "text/html")

    def test_http_equiv_meta_is_used(self) -> None:
        raw = (
            '<html><head><meta http-equiv="Content-Type" '
            'content="text/html; charset=iso-8859-1"></head><body><p>café</p></body></html>'
        ).encode("iso-8859-1")
        assert "café" in decode_html(raw, None)

    def test_a_utf8_bom_is_removed(self) -> None:
        raw = "\ufeff<html><body><p>x</p></body></html>".encode("utf-8")
        assert decode_html(raw, "text/html").startswith("<html>")

    def test_the_default_is_utf8(self) -> None:
        assert "記事" in decode_html("<p>記事</p>".encode("utf-8"), None)

    def test_an_unknown_charset_falls_back_to_utf8(self) -> None:
        assert "x" in decode_html(b"<p>x</p>", "text/html; charset=not-a-charset")

    def test_undecodable_bytes_do_not_raise(self) -> None:
        assert decode_html(b"<p>\xff\xfe</p>", "text/html; charset=utf-8")

    def test_the_same_bytes_decode_to_the_same_text(self) -> None:
        raw = "<p>記事</p>".encode("utf-8")
        assert decode_html(raw, None) == decode_html(raw, None)


class TestResultTypes:
    def test_a_page_is_hashable_and_frozen(self) -> None:
        fetched = FetchedPage(
            requested_url="https://example.com/a",
            final_url="https://example.com/a",
            html="<p>x</p>",
            content_type="text/html",
        )
        with pytest.raises(Exception):
            fetched.html = "<p>y</p>"  # type: ignore[misc]

    def test_a_failure_carries_a_machine_readable_kind(self) -> None:
        failure = FetchFailure(kind=ErrorKind.TIMEOUT, detail="read timed out after 20s")
        assert failure.kind is ErrorKind.TIMEOUT
        assert failure.to_error_record().kind is ErrorKind.TIMEOUT

    def test_a_failure_detail_is_bounded(self) -> None:
        failure = FetchFailure(kind=ErrorKind.NETWORK, detail="x" * 5000)
        assert len(failure.to_error_record().detail or "") <= 500

    def test_the_byte_cap_is_declared(self) -> None:
        assert MAX_BODY_BYTES > 0


def resolver(*addresses: str):
    """A `socket.getaddrinfo` stand-in, so no test in this file resolves a name."""

    def resolve(host: str, port: object) -> list:
        if not addresses:
            raise socket.gaierror("no address")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0)) for address in addresses]

    return resolve


class TestPublicAddressGuard:
    """A feed must not be able to point the fetcher at the network it runs in."""

    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",  # the cloud metadata address
            "0.0.0.0",
            "100.64.0.1",
            "224.0.0.1",
            "::1",
            "fc00::1",
            "fe80::1",
        ],
    )
    def test_a_non_public_address_is_refused(self, address: str) -> None:
        assert not is_public_address("host.example", resolve=resolver(address))

    @pytest.mark.parametrize(
        "address", ["93.184.216.34", "8.8.8.8", "2606:2800:220:1:248:1893:25c8:1946"]
    )
    def test_a_public_address_is_allowed(self, address: str) -> None:
        assert is_public_address("host.example", resolve=resolver(address))

    def test_one_non_public_address_refuses_the_host(self) -> None:
        assert not is_public_address("host.example", resolve=resolver("93.184.216.34", "127.0.0.1"))

    def test_a_scoped_ipv6_address_is_read(self) -> None:
        assert not is_public_address("host.example", resolve=resolver("fe80::1%en0"))

    def test_a_host_that_does_not_resolve_is_refused(self) -> None:
        assert not is_public_address("host.example", resolve=resolver())

    def test_a_host_that_resolves_to_nothing_is_refused(self) -> None:
        assert not is_public_address("host.example", resolve=lambda host, port: [])


class TestBlockedNetworkTable:
    """The verdict must come from this module's table, never from the interpreter.

    `ipaddress.is_global` moved between 3.10 and 3.11, so delegating to it made
    the same address public on one interpreter and private on another. Every
    literal below is pinned so a later stdlib change cannot move the boundary
    without a test failing. `requires-python` allows 3.10, so both ends matter.
    """

    @pytest.mark.parametrize(
        ("address", "why"),
        [
            ("2002:7f00:1::", "6to4 wrapping 127.0.0.1; public on 3.10"),
            ("2002:a00:1::", "6to4 wrapping 10.0.0.1; public on 3.10"),
            ("2002:808:808::", "6to4 at all, deprecated by RFC 7526"),
            ("3fff::1", "documentation, RFC 9637; public on 3.10"),
            ("2001:20::1", "ORCHIDv2; public on 3.11 and later"),
            ("2001::1", "Teredo"),
            ("2001:2::1", "benchmarking"),
            ("2001:db8::1", "documentation"),
            ("::7f00:1", "IPv4-compatible form of 127.0.0.1; public on every version"),
            ("::a00:1", "IPv4-compatible form of 10.0.0.1"),
            ("64:ff9b::7f00:1", "NAT64 wrapping 127.0.0.1; public on every version"),
            ("64:ff9b::a9fe:a9fe", "NAT64 wrapping the metadata address"),
            ("64:ff9b:1::1", "local-use translation"),
            ("::ffff:127.0.0.1", "IPv4-mapped loopback"),
            ("::ffff:169.254.169.254", "IPv4-mapped metadata address"),
            ("5f00::1", "segment routing SIDs"),
            ("100::1", "discard-only"),
            ("192.88.99.1", "6to4 relay anycast, deprecated by RFC 7526"),
            ("192.0.0.1", "IETF protocol assignments"),
            ("192.0.2.1", "documentation, TEST-NET-1"),
            ("198.51.100.1", "documentation, TEST-NET-2"),
            ("203.0.113.1", "documentation, TEST-NET-3"),
            ("198.18.0.1", "benchmarking"),
            ("240.0.0.1", "reserved"),
            ("255.255.255.255", "limited broadcast"),
            ("ff02::1", "multicast"),
        ],
    )
    def test_a_blocked_literal_is_refused_on_every_version(self, address: str, why: str) -> None:
        assert not is_public_address("host.example", resolve=resolver(address)), why

    @pytest.mark.parametrize(
        ("address", "why"),
        [
            ("8.8.8.8", "ordinary public IPv4"),
            ("93.184.216.34", "ordinary public IPv4"),
            ("2606:2800:220:1:248:1893:25c8:1946", "ordinary public IPv6"),
            ("2620:4f:8000::1", "direct delegation AS112, globally reachable"),
            ("::ffff:8.8.8.8", "IPv4-mapped public address stays reachable"),
            ("64:ff9b::808:808", "NAT64 wrapping a public address stays reachable"),
        ],
    )
    def test_a_public_literal_is_allowed_on_every_version(self, address: str, why: str) -> None:
        assert is_public_address("host.example", resolve=resolver(address)), why

    def test_the_verdict_survives_a_stdlib_that_disagrees(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Make `is_global` lie in both directions. The table must still decide."""
        for verdict in (True, False):
            for family in (ipaddress.IPv4Address, ipaddress.IPv6Address):
                monkeypatch.setattr(family, "is_global", property(lambda self, v=verdict: v))
            assert not is_public_address("h.example", resolve=resolver("2002:7f00:1::"))
            assert not is_public_address("h.example", resolve=resolver("127.0.0.1"))
            assert is_public_address("h.example", resolve=resolver("8.8.8.8"))
            assert is_public_address(
                "h.example", resolve=resolver("2606:2800:220:1:248:1893:25c8:1946")
            )

    def test_every_blocked_entry_is_a_network_of_its_family(self) -> None:
        for network in BLOCKED_IPV4_NETWORKS:
            assert isinstance(network, ipaddress.IPv4Network)
        for network in (*BLOCKED_IPV6_NETWORKS, *TRANSLATED_IPV6_NETWORKS):
            assert isinstance(network, ipaddress.IPv6Network)


class TestRedirectGuard:
    def handler(self) -> _GuardedRedirectHandler:
        return _GuardedRedirectHandler()

    @pytest.mark.parametrize(
        "target",
        [
            "http://127.0.0.1/admin",
            "https://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "file:///etc/passwd",
            "ftp://example.com/a",
            "javascript:alert(1)",
        ],
    )
    def test_a_redirect_to_a_blocked_target_is_refused(self, target: str) -> None:
        with pytest.raises(BlockedTarget):
            self.handler().redirect_request(None, None, 302, "Found", {}, target)

    def test_the_redirect_cap_is_lower_than_the_urllib_default(self) -> None:
        assert _GuardedRedirectHandler.max_redirections == MAX_REDIRECTS
        assert MAX_REDIRECTS < urllib.request.HTTPRedirectHandler.max_redirections


class TestFetchPageRefusesBlockedTargets:
    """`fetch_page` returns a failure rather than opening the connection."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/admin",
            "https://169.254.169.254/latest/meta-data/iam/security-credentials/",
            "http://[::1]:8080/",
            "http://10.0.0.5/internal",
        ],
    )
    def test_a_non_public_url_is_not_opened(self, url: str) -> None:
        outcome = fetch_page(url, timeout=0.01)
        assert isinstance(outcome, FetchFailure)
        assert outcome.kind is ErrorKind.NETWORK
        assert "blocked target" in (outcome.detail or "")

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/a"])
    def test_another_scheme_is_not_opened(self, url: str) -> None:
        outcome = fetch_page(url, timeout=0.01)
        assert isinstance(outcome, FetchFailure)
        assert "blocked target" in (outcome.detail or "")
