"""Body fetching helpers. No test in this file opens a network connection."""

from __future__ import annotations

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
from digest_normalize.fetching import _GuardedRedirectHandler


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
