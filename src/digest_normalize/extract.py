"""Extract only the body, headings, code, author and publication date of a page.

A collected page is untrusted input. Nothing here follows, resolves or executes
what a page says: the parser reads element text and a fixed list of metadata
attributes, and every other channel a page can write into - scripts, comments,
hidden elements, `alt` and `title` attributes, site chrome - is dropped before
the text is assembled.

Visible text is kept verbatim, including sentences that read as instructions.
Removing them would hide from the Selector what the article actually says; the
boundary that stops them being read as instructions is `boundary.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from ._text import collapse_text, is_blank, normalize_block

MAX_AUTHOR_CHARS = 100
"""An author is a name. A longer value is prose that landed in the wrong tag."""

_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)

_SKIP_TAGS = frozenset(
    {
        # executable or non-textual
        "script", "style", "noscript", "template", "svg", "math", "iframe", "object", "embed", "canvas",
        # interactive
        "form", "select", "textarea", "button", "input", "label",
        # site chrome. A byline inside <header> is lost with it; the feed
        # already carries the title, and the author comes from <meta>.
        "nav", "header", "footer", "aside",
        # metadata, read through `_read_metadata` rather than as text
        "head", "title",
    }
)

_METADATA_CONTAINERS = frozenset({"head", "title"})
"""Skipped as text, yet still where a page is allowed to declare its metadata.

Every other skipped element is a channel the reader never sees, so a `<meta>`,
`<link rel=canonical>` or `<time>` planted inside one is not read either.
"""

_HEADINGS = {f"h{level}": level for level in range(1, 7)}

_BLOCK_TAGS = frozenset(
    {
        "address", "article", "blockquote", "dd", "div", "dl", "dt", "fieldset", "figcaption", "figure",
        "hr", "li", "main", "ol", "p", "pre", "section", "table", "tbody", "td", "th", "thead", "tr", "ul",
        *_HEADINGS,
    }
)

_HIDDEN_CLASSES = ("sr-only", "visually-hidden", "visuallyhidden", "screen-reader", "skip-link")

# A zero length, written any of the ways CSS allows, up to the next declaration.
_ZERO = r"(?:0+(?:\.0+)?|\.0+)(?:px|pt|em|rem|ex|ch|vw|vh|vmin|vmax|cm|mm|in|pc|%)?\s*(?:;|!|$)"

_HIDDEN_STYLES = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"display\s*:\s*none",
        r"visibility\s*:\s*hidden",
        rf"opacity\s*:\s*{_ZERO}",
        rf"font-size\s*:\s*{_ZERO}",
        # Moved far enough out of the viewport to be off-screen rather than nudged.
        r"text-indent\s*:\s*-\s*\d{3,}",
        r"(?<![-\w])(?:left|right|top|bottom)\s*:\s*-\s*\d{3,}",
        r"clip-path\s*:\s*inset\(\s*(?:50|100)%",
        r"clip\s*:\s*rect\(\s*0",
    )
)
"""Declarations that put text outside what a reader sees.

Text hidden this way is a channel for instructions and it counts toward the
primary-information floor, so it is dropped the same way `display:none` is.
Text hidden by painting it the colour of its background is not detected: that
needs the cascade and a colour model, neither of which this parser has.
"""

_ZERO_BOX = re.compile(rf"(?<![-\w])(?:max-)?(?:width|height)\s*:\s*{_ZERO}", re.IGNORECASE)
_CLIPPED_OVERFLOW = re.compile(r"overflow(?:-[xy])?\s*:\s*hidden", re.IGNORECASE)

_PARAGRAPH = "paragraph"
_LIST_ITEM = "list_item"
_QUOTE = "quote"
_CODE = "code"
_TABLE_ROW = "table_row"
_HEADING = "heading"

# Blocks of the same kind that follow each other belong to one list or table.
_GLUED = frozenset({_LIST_ITEM, _TABLE_ROW})


@dataclass(frozen=True)
class ExtractedDocument:
    """What a page contributed, after everything else was dropped.

    `published_at_raw` is reported but not used to build the article: the
    `required_fields` gate needs a date before the page is fetched, so the
    collected item is the only source that can decide that gate.
    """

    body_text: str
    author: str | None = None
    published_at_raw: str | None = None
    canonical_url_raw: str | None = None
    heading_count: int = 0
    code_block_count: int = 0


@dataclass
class _Block:
    kind: str
    text: str
    article_id: int | None
    in_main: bool
    in_role_main: bool


@dataclass
class _Open:
    tag: str
    skipped: bool = False
    hides_metadata: bool = False
    article_id: int | None = None
    is_main: bool = False
    is_role_main: bool = False


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_Block] = []
        self.author: str | None = None
        self.published_at_raw: str | None = None
        self.canonical_url_raw: str | None = None
        self._stack: list[_Open] = []
        self._buffer: list[str] = []
        self._cells: list[str] = []
        self._kind = _PARAGRAPH
        self._heading_level = 0
        self._articles = 0

    # -- structure ------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): (value or "") for name, value in attrs}
        if not self._hiding_metadata:
            self._read_metadata(tag, values)

        if tag in _VOID_TAGS:
            if tag == "br" and not self._skipping:
                self._buffer.append(" ")
            return

        hidden = _is_hidden(values)
        skipped = self._skipping or tag in _SKIP_TAGS or hidden
        # `<head>` is skipped as text but is where metadata belongs, so the two
        # reasons to stop reading are tracked apart.
        hides_metadata = (
            self._hiding_metadata or hidden or (tag in _SKIP_TAGS and tag not in _METADATA_CONTAINERS)
        )
        # Whatever is buffered belongs to the element that is still open.
        if skipped or tag in _BLOCK_TAGS:
            self._flush()

        open_tag = _Open(tag, skipped=skipped, hides_metadata=hides_metadata)
        if not skipped:
            if tag == "article":
                self._articles += 1
                open_tag.article_id = self._articles
            open_tag.is_main = tag == "main"
            open_tag.is_role_main = values.get("role", "").strip().lower() == "main"
        self._stack.append(open_tag)

        if skipped:
            return
        if tag in _BLOCK_TAGS:
            self._kind = _kind_of(tag)
            self._heading_level = _HEADINGS.get(tag, 0)
        elif tag == "code" and self._kind != _CODE:
            self._buffer.append("`")

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        if tag == "code" and self._kind != _CODE and not self._skipping:
            self._buffer.append("`")
        if tag in _BLOCK_TAGS and not self._skipping:
            self._flush()
            if tag == "tr":
                self._flush_row()
        self._close(tag)
        self._recompute_kind()

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self._buffer.append(data)

    def handle_comment(self, data: str) -> None:
        """Comments are a channel readers never see. They are dropped."""

    def close(self) -> None:
        """Flush what the last element left buffered, including an unclosed one."""
        super().close()
        self._flush()
        self._flush_row()

    # -- metadata -------------------------------------------------------
    def _read_metadata(self, tag: str, values: dict[str, str]) -> None:
        if tag == "meta":
            name = (values.get("name") or values.get("property") or "").strip().lower()
            content = values.get("content", "")
            if name in ("author", "article:author") and self.author is None:
                self.author = _author_or_none(content)
            elif name in ("article:published_time", "date", "citation_publication_date", "pubdate"):
                self._set_published(content)
        elif tag == "link":
            rel = values.get("rel", "").strip().lower()
            if rel == "canonical" and self.canonical_url_raw is None and not is_blank(values.get("href")):
                self.canonical_url_raw = values["href"].strip()
        elif tag == "time":
            self._set_published(values.get("datetime", ""))

    def _set_published(self, content: str) -> None:
        if self.published_at_raw is None and not is_blank(content):
            self.published_at_raw = content.strip()

    # -- buffering ------------------------------------------------------
    @property
    def _skipping(self) -> bool:
        return bool(self._stack) and self._stack[-1].skipped

    @property
    def _hiding_metadata(self) -> bool:
        return bool(self._stack) and self._stack[-1].hides_metadata

    def _flush(self) -> None:
        raw = "".join(self._buffer)
        self._buffer.clear()
        if self._kind == _CODE:
            text = normalize_block(raw)
        else:
            text = collapse_text(raw)
        if not text:
            return
        if self._kind == _TABLE_ROW:
            self._cells.append(text)
            return
        self._emit(self._kind, _render(self._kind, text, self._heading_level))

    def _flush_row(self) -> None:
        if not self._cells:
            return
        self._emit(_TABLE_ROW, " | ".join(self._cells))
        self._cells.clear()

    def _emit(self, kind: str, text: str) -> None:
        # Where the block sits is recorded now, while its element is still open.
        self.blocks.append(
            _Block(
                kind=kind,
                text=text,
                article_id=self._enclosing_article,
                in_main=any(frame.is_main for frame in self._stack),
                in_role_main=any(frame.is_role_main for frame in self._stack),
            )
        )

    @property
    def _enclosing_article(self) -> int | None:
        for frame in self._stack:
            if frame.article_id is not None:
                return frame.article_id
        return None

    def _recompute_kind(self) -> None:
        # The kind belongs to the innermost block element still open, so closing
        # an inline element such as `<code>` inside a list item keeps the item.
        for frame in reversed(self._stack):
            if frame.tag in _BLOCK_TAGS:
                self._kind = _kind_of(frame.tag)
                self._heading_level = _HEADINGS.get(frame.tag, 0)
                return
        self._kind = _PARAGRAPH
        self._heading_level = 0

    def _close(self, tag: str) -> None:
        for position in range(len(self._stack) - 1, -1, -1):
            if self._stack[position].tag == tag:
                del self._stack[position:]
                return
        # An end tag with no matching start tag: broken markup, and ignoring it
        # keeps the rest of the page readable.


def extract_document(html: str) -> ExtractedDocument:
    """Parse `html` and return only the parts this layer is allowed to keep."""
    parser = _Extractor()
    parser.feed(html)
    parser.close()

    blocks = _content_root(parser.blocks)
    return ExtractedDocument(
        body_text=_join(blocks),
        author=parser.author,
        published_at_raw=parser.published_at_raw,
        canonical_url_raw=parser.canonical_url_raw,
        heading_count=sum(1 for block in blocks if block.kind == _HEADING),
        code_block_count=sum(1 for block in blocks if block.kind == _CODE),
    )


def _content_root(blocks: list[_Block]) -> list[_Block]:
    # A permalink page usually has one <article>; an index page has several, and
    # the first one is the entry the feed pointed at.
    first_article = [block for block in blocks if block.article_id == 1]
    if first_article:
        return first_article
    for selector in (lambda b: b.in_main, lambda b: b.in_role_main):
        selected = [block for block in blocks if selector(block)]
        if selected:
            return selected
    return blocks


def _join(blocks: list[_Block]) -> str:
    out: list[str] = []
    for position, block in enumerate(blocks):
        if position:
            previous = blocks[position - 1]
            glue = "\n" if block.kind == previous.kind and block.kind in _GLUED else "\n\n"
            out.append(glue)
        out.append(block.text)
    return "".join(out)


def _render(kind: str, text: str, heading_level: int) -> str:
    if kind == _HEADING:
        return f"{'#' * heading_level} {text}"
    if kind == _LIST_ITEM:
        return f"- {text}"
    if kind == _QUOTE:
        return f"> {text}"
    if kind == _CODE:
        return f"```\n{text}\n```"
    return text


def _kind_of(tag: str) -> str:
    if tag in _HEADINGS:
        return _HEADING
    if tag == "li":
        return _LIST_ITEM
    if tag == "blockquote":
        return _QUOTE
    if tag == "pre":
        return _CODE
    if tag in ("td", "th"):
        return _TABLE_ROW
    return _PARAGRAPH


def _is_hidden(values: dict[str, str]) -> bool:
    if "hidden" in values:
        return True
    if values.get("aria-hidden", "").strip().lower() == "true":
        return True
    if _is_hidden_style(values.get("style", "")):
        return True
    classes = values.get("class", "").lower().split()
    return any(name in classes for name in _HIDDEN_CLASSES)


def _is_hidden_style(style: str) -> bool:
    if any(pattern.search(style) for pattern in _HIDDEN_STYLES):
        return True
    # A zero-sized box hides what it holds only when the overflow is clipped.
    return bool(_ZERO_BOX.search(style) and _CLIPPED_OVERFLOW.search(style))


def _author_or_none(content: str) -> str | None:
    author = collapse_text(content)
    if not author or len(author) > MAX_AUTHOR_CHARS:
        return None
    # `article:author` often holds a profile URL rather than a name.
    if author.startswith(("http://", "https://")):
        return None
    return author
