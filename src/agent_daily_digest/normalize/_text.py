"""Text rules shared by every normalization step.

`NonBlankStr` in the contracts accepts a zero-width space as content, so this
module defines the stricter notion of "blank" the gates use, and the single
whitespace form every extracted string is reduced to.
"""

from __future__ import annotations

import unicodedata

INVISIBLE = "\u200b\u200c\u200d\u2060\ufeff\u00ad"
"""Characters that carry no meaning in this corpus and are removed outright.

Zero-width joiner and non-joiner are included: the digest covers English and
Japanese technical writing, where they do not change a word, and leaving them in
would let two visually identical bodies hash differently.
"""

_INVISIBLE_MAP = {ord(ch): None for ch in INVISIBLE}

# Unicode whitespace that is not already handled by `str.split()`. Kept explicit
# so the mapping cannot change with the Python version.
_SPACE_MAP = {
    ord(ch): " "
    for ch in "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009"
    "\u200a\u202f\u205f\u3000"
}


def strip_invisible(value: str) -> str:
    """Remove the characters listed in `INVISIBLE`."""
    return value.translate(_INVISIBLE_MAP)


def is_blank(value: str | None) -> bool:
    """True when the value carries no readable character.

    `None`, the empty string, whitespace of any width, and invisible characters
    are all blank. This is what `missing_*` gate reasons mean.
    """
    if value is None:
        return True
    return not strip_invisible(value).strip()


def collapse_text(value: str) -> str:
    """Reduce a run of inline text to its single normalized form.

    NFC, invisible characters removed, every kind of whitespace collapsed to one
    space, stripped at both ends. Applying it twice changes nothing.
    """
    text = unicodedata.normalize("NFC", value)
    text = strip_invisible(text).translate(_SPACE_MAP)
    return " ".join(text.split())


def normalize_block(value: str) -> str:
    """Like `collapse_text`, but line breaks inside the block are kept.

    Used for preformatted code, where the line structure is part of the content.
    Trailing whitespace on each line is dropped and blank lines at both ends are
    removed, so the same code block always hashes the same way.
    """
    text = unicodedata.normalize("NFC", value)
    text = strip_invisible(text).translate(_SPACE_MAP)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)
