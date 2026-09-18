"""Canonical URL rules. The result is written to the Git-tracked processing state."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from digest_contracts import HttpUrlStr
from digest_normalize import UrlProblem, UrlRejected, canonicalize_url

_URL = TypeAdapter(HttpUrlStr)


def reason(value: str | None) -> UrlProblem:
    with pytest.raises(UrlRejected) as excinfo:
        canonicalize_url(value)
    return excinfo.value.problem


class TestMissing:
    @pytest.mark.parametrize("value", [None, "", "   ", "\u3000", "\u200b"])
    def test_blank_url_is_missing(self, value: str | None) -> None:
        assert reason(value) is UrlProblem.MISSING


class TestInvalid:
    @pytest.mark.parametrize(
        "value",
        [
            "javascript:alert(1)",
            "file:///etc/passwd",
            "data:text/html,<p>x</p>",
            "ftp://example.com/a",
            "//example.com/a",
            "/posts/1",
            "example.com/a",
            "https://",
            "https:///posts/1",
            "http://user:pw@example.com/a",
            "http://user@example.com/a",
            "https://exa mple.com/a",
            "https://example.com/a\nb",
            "https://example.com/\x00",
        ],
    )
    def test_rejected(self, value: str) -> None:
        assert reason(value) is UrlProblem.INVALID

    def test_over_2048_characters_is_rejected(self) -> None:
        assert reason("https://example.com/" + "a" * 2100) is UrlProblem.INVALID


class TestCanonicalForm:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # scheme and host case
            ("HTTPS://Example.COM/Posts/1", "https://example.com/Posts/1"),
            ("https://example.com.", "https://example.com/"),
            # default ports
            ("https://example.com:443/a", "https://example.com/a"),
            ("http://example.com:80/a", "http://example.com/a"),
            ("https://example.com:8443/a", "https://example.com:8443/a"),
            # empty path
            ("https://example.com", "https://example.com/"),
            ("https://example.com?a=1", "https://example.com/?a=1"),
            # fragment
            ("https://example.com/a#section-2", "https://example.com/a"),
            ("https://example.com/a#", "https://example.com/a"),
            # dot segments
            ("https://example.com/a/b/../c", "https://example.com/a/c"),
            ("https://example.com/./a", "https://example.com/a"),
            # percent-encoding
            ("https://example.com/a%2Fb", "https://example.com/a%2Fb"),
            ("https://example.com/a%2fb", "https://example.com/a%2Fb"),
            ("https://example.com/%7Euser", "https://example.com/~user"),
            # empty query
            ("https://example.com/a?", "https://example.com/a"),
            # surrounding whitespace and invisible characters
            ("  https://example.com/a  ", "https://example.com/a"),
            ("https://example.com/a\u200b", "https://example.com/a"),
        ],
    )
    def test_canonical(self, raw: str, expected: str) -> None:
        assert canonicalize_url(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("https://example.com/a?utm_source=feed", "https://example.com/a"),
            ("https://example.com/a?UTM_Source=feed", "https://example.com/a"),
            ("https://example.com/a?utm_source=feed&id=7", "https://example.com/a?id=7"),
            ("https://example.com/a?id=7&utm_medium=rss", "https://example.com/a?id=7"),
            ("https://example.com/a?fbclid=x&gclid=y", "https://example.com/a"),
            ("https://example.com/a?ref_src=twsrc&b=2", "https://example.com/a?b=2"),
            # order of the remaining parameters is preserved, not sorted
            ("https://example.com/a?b=2&a=1", "https://example.com/a?b=2&a=1"),
            # a parameter without a value is kept
            ("https://example.com/a?draft", "https://example.com/a?draft"),
        ],
    )
    def test_tracking_parameters_are_dropped(self, raw: str, expected: str) -> None:
        assert canonicalize_url(raw) == expected

    def test_trailing_slash_is_significant(self) -> None:
        assert canonicalize_url("https://example.com/a/") == "https://example.com/a/"
        assert canonicalize_url("https://example.com/a") == "https://example.com/a"

    def test_result_satisfies_the_contract_type(self) -> None:
        assert _URL.validate_python(canonicalize_url("HTTPS://Example.com:443/a#x"))

    @pytest.mark.parametrize(
        "raw",
        [
            "HTTPS://Example.COM:443/a/b/../c?utm_source=feed&id=7#frag",
            "https://example.com/%7Euser/?utm_medium=rss",
        ],
    )
    def test_is_idempotent(self, raw: str) -> None:
        once = canonicalize_url(raw)
        assert canonicalize_url(once) == once
