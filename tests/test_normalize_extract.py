"""HTML extraction: body, headings, code, author and publication date only."""

from __future__ import annotations

import pytest

from digest_normalize import extract_document
from normalize_helpers import read_html

BASIC_BODY = (
    "# Harness retry budget\n"
    "\n"
    "We capped agent retries at `3` per stage and measured the change.\n"
    "\n"
    "## What we changed\n"
    "\n"
    "- Retry budget per stage\n"
    "- Backoff without jitter\n"
    "\n"
    "```\n"
    "budget = RetryBudget(max_attempts=3)\n"
    "runner.attach(budget)\n"
    "```\n"
    "\n"
    "> Cutting retries removed 41% of wasted tokens.\n"
    "\n"
    "Stage | Before | After\n"
    "select | 9.2s | 6.1s\n"
    "\n"
    "The full configuration lives in the repository."
)


@pytest.fixture()
def basic():
    return extract_document(read_html("article_basic"))


class TestExtractedFields:
    def test_body_text_is_exact(self, basic) -> None:
        assert basic.body_text == BASIC_BODY

    def test_headings_are_kept_with_their_level(self, basic) -> None:
        assert "# Harness retry budget" in basic.body_text
        assert "## What we changed" in basic.body_text
        assert basic.heading_count == 2

    def test_code_is_kept_in_a_fenced_block(self, basic) -> None:
        assert "```\nbudget = RetryBudget(max_attempts=3)\nrunner.attach(budget)\n```" in basic.body_text
        assert basic.code_block_count == 1

    def test_inline_code_keeps_backticks(self, basic) -> None:
        assert "retries at `3` per stage" in basic.body_text

    def test_author_comes_from_meta(self, basic) -> None:
        assert basic.author == "Synthetic Author"

    def test_published_at_is_extracted_raw(self, basic) -> None:
        assert basic.published_at_raw == "2026-09-16T22:15:00+09:00"

    def test_canonical_link_is_extracted_raw(self, basic) -> None:
        assert basic.canonical_url_raw == "https://example.com/posts/retry-budget?utm_source=feed"


class TestOnlyTheAllowedParts:
    @pytest.mark.parametrize(
        "text",
        [
            "window.analytics",  # script
            "color: red",  # style
            "Archive",  # nav
            "Example Engineering Blog",  # header
            "Related posts",  # aside
            "Copyright Example",  # footer
            "Harness retry budget - Example Engineering",  # title element
        ],
    )
    def test_non_content_is_dropped(self, basic, text: str) -> None:
        assert text not in basic.body_text

    def test_the_first_article_element_is_the_content_root(self) -> None:
        html = (
            "<html><body><p>lead-in</p>"
            "<article><p>first article body</p></article>"
            "<article><p>second article body</p></article>"
            "</body></html>"
        )
        assert extract_document(html).body_text == "first article body"

    def test_main_is_used_when_there_is_no_article(self) -> None:
        html = "<html><body><p>lead-in</p><main><p>main body</p></main></body></html>"
        assert extract_document(html).body_text == "main body"

    def test_role_main_is_used_when_there_is_no_article_or_main(self) -> None:
        html = '<html><body><p>lead-in</p><div role="main"><p>role body</p></div></body></html>'
        assert extract_document(html).body_text == "role body"

    def test_the_whole_body_is_used_when_there_is_no_wrapper(self) -> None:
        extracted = extract_document(read_html("article_no_wrapper"))
        assert extracted.body_text.startswith("This page has no article or main element")
        assert "41 percent" in extracted.body_text

    def test_a_page_without_body_text_extracts_to_an_empty_string(self) -> None:
        assert extract_document("<html><head><title>t</title></head><body></body></html>").body_text == ""

    def test_broken_markup_does_not_raise(self) -> None:
        extracted = extract_document("<html><body><article><p>unclosed paragraph<div></article>")
        assert "unclosed paragraph" in extracted.body_text


class TestMetadataEdgeCases:
    def test_a_blank_author_is_dropped(self) -> None:
        html = '<html><head><meta name="author" content="  "></head><body><p>x</p></body></html>'
        assert extract_document(html).author is None

    def test_an_over_long_author_is_dropped(self) -> None:
        html = f'<html><head><meta name="author" content="{"a" * 101}"></head><body><p>x</p></body></html>'
        assert extract_document(html).author is None

    def test_author_whitespace_is_collapsed(self) -> None:
        html = '<html><head><meta name="author" content=" Ada   Lovelace\n"></head><body><p>x</p></body></html>'
        assert extract_document(html).author == "Ada Lovelace"

    def test_article_author_meta_is_the_second_choice(self) -> None:
        html = (
            '<html><head><meta property="article:author" content="Grace Hopper">'
            "</head><body><p>x</p></body></html>"
        )
        assert extract_document(html).author == "Grace Hopper"

    def test_an_author_url_is_not_used_as_a_name(self) -> None:
        html = (
            '<html><head><meta property="article:author" content="https://example.com/authors/7">'
            "</head><body><p>x</p></body></html>"
        )
        assert extract_document(html).author is None

    def test_time_element_is_the_last_choice_for_the_date(self) -> None:
        html = '<html><body><article><time datetime="2026-09-15T00:00:00Z">Sep 15</time><p>x</p></article></body></html>'
        assert extract_document(html).published_at_raw == "2026-09-15T00:00:00Z"

    def test_meta_date_is_extracted(self) -> None:
        assert extract_document(read_html("article_no_wrapper")).published_at_raw == "2026-09-15"

    def test_missing_metadata_is_none(self) -> None:
        extracted = extract_document(read_html("article_thin"))
        assert extracted.author is None
        assert extracted.published_at_raw is None
        assert extracted.canonical_url_raw is None


class TestDeterminism:
    @pytest.mark.parametrize(
        "name", ["article_basic", "article_injection", "article_thin", "article_no_wrapper"]
    )
    def test_the_same_html_extracts_to_the_same_text(self, name: str) -> None:
        html = read_html(name)
        assert extract_document(html) == extract_document(html)

    def test_windows_line_endings_do_not_change_the_result(self) -> None:
        html = read_html("article_basic")
        assert extract_document(html.replace("\n", "\r\n")).body_text == BASIC_BODY
