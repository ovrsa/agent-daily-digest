"""Markdown primitives shared by the digest body and the index.

Everything here is a pure string transform. The renderer never edits the
wording it is given; these helpers only decide how a value is placed in
Markdown so the same input always produces the same bytes.
"""

from __future__ import annotations

_LINK_TEXT_ESCAPES = str.maketrans({"\\": "\\\\", "[": "\\[", "]": "\\]"})

# `<` and `>` are not valid in a URL (RFC 3986); percent-encoding them is the
# only way to keep an angle-bracket destination parseable.
_DESTINATION_ESCAPES = str.maketrans({"<": "%3C", ">": "%3E"})

_DESTINATION_NEEDS_BRACKETS = "()<> "


def inline(text: str) -> str:
    """Collapse every whitespace run to a single space.

    A contract field is `NonBlankStr`, so it may carry newlines. A raw newline
    inside a list item or a heading would change the document structure, and a
    trailing space would change the bytes without changing the reading.
    """
    return " ".join(text.split())


def link(text: str, url: str) -> str:
    """Render an inline link, keeping `url` byte-identical wherever it is legal."""
    return f"[{inline(text).translate(_LINK_TEXT_ESCAPES)}]({_destination(url)})"


def _destination(url: str) -> str:
    if any(ch in url for ch in _DESTINATION_NEEDS_BRACKETS):
        return f"<{url.translate(_DESTINATION_ESCAPES)}>"
    return url
