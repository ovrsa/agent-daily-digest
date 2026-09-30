"""Blankness and whitespace rules shared by every normalization step."""

from __future__ import annotations

import pytest

from agent_daily_digest.content.text import collapse_text, is_blank, strip_invisible


class TestIsBlank:
    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            " ",
            "\t\n\r",
            "\u00a0",  # no-break space
            "\u3000",  # ideographic space
            "\u200b",  # zero width space, which NonBlankStr accepts
            "\ufeff",
            "\u2060",
            "\u00ad",  # soft hyphen
            "\u200b \u3000\ufeff",
        ],
    )
    def test_blank_values(self, value: str | None) -> None:
        assert is_blank(value)

    @pytest.mark.parametrize("value", ["a", " a ", "\u200ba", "0", "-", "記事"])
    def test_non_blank_values(self, value: str) -> None:
        assert not is_blank(value)


class TestStripInvisible:
    def test_removes_zero_width_and_soft_hyphen(self) -> None:
        assert strip_invisible("re\u200btry\u00adbud\ufeffget") == "retrybudget"

    def test_keeps_ordinary_characters(self) -> None:
        assert strip_invisible("retry budget 記事") == "retry budget 記事"


class TestCollapseText:
    def test_collapses_runs_and_strips(self) -> None:
        assert collapse_text("  a \t\n b   c  ") == "a b c"

    def test_unicode_spaces_become_ascii_space(self) -> None:
        assert collapse_text("a\u00a0b\u3000c\u2009d") == "a b c d"

    def test_applies_nfc(self) -> None:
        # U+304B plus the combining voiced mark U+3099 is the same character as
        # the composed U+304C. Without NFC the two would hash differently.
        assert collapse_text("\u304b\u3099") == "\u304c"

    def test_removes_invisible_characters(self) -> None:
        assert collapse_text("re\u200btry") == "retry"

    def test_is_idempotent(self) -> None:
        once = collapse_text(" a\u3000 b\u200bc ")
        assert collapse_text(once) == once
