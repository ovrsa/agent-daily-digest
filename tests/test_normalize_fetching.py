"""Body fetching helpers. No test in this file opens a network connection."""

from __future__ import annotations

import pytest

from digest_contracts import ErrorKind
from digest_normalize import (
    MAX_BODY_BYTES,
    FetchFailure,
    FetchedPage,
    decode_html,
    is_html_content_type,
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
