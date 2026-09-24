"""Addressed paragraphs, and the untrusted blocks a model reads them in.

`digest_normalize` joins the blocks it keeps with a blank line, glues list items
and table rows with a single newline, and fences code with three backticks. A
paragraph here is one of those blocks: splitting on blank lines recovers them,
except inside a fenced code block, whose own blank lines belong to the code.

The split is a pure function of the body text, so the same body always yields
the same paragraph ids and an Evidence ID resolves the same way on every run.

Everything a page wrote - title, author, links, paragraphs - reaches the model
inside one delimited block, through `escape_untrusted`, the same boundary
`digest_normalize.as_untrusted_block` uses.
"""

from __future__ import annotations

from collections.abc import Collection

from digest_contracts import NormalizedArticle, Paragraph, SourceDocument
from digest_normalize import HEADER, UNTRUSTED_CLOSE, UNTRUSTED_OPEN, escape_untrusted

_FENCE = "```"


def split_paragraphs(body_text: str) -> tuple[Paragraph, ...]:
    chunks: list[str] = []
    fenced: list[str] | None = None
    for chunk in body_text.split("\n\n"):
        if fenced is not None:
            fenced.append(chunk)
            if chunk.rstrip().endswith(_FENCE):
                chunks.append("\n\n".join(fenced))
                fenced = None
            continue
        if chunk.startswith(_FENCE) and not _fence_closed(chunk):
            fenced = [chunk]
            continue
        chunks.append(chunk)
    if fenced is not None:
        chunks.append("\n\n".join(fenced))

    paragraphs: list[Paragraph] = []
    section: str | None = None
    for chunk in chunks:
        text = chunk.strip()
        if not text:
            continue
        if _is_heading(text):
            section = text.lstrip("#").strip() or section
        paragraphs.append(Paragraph(paragraph_id=f"p{len(paragraphs) + 1}", section=section, text=text))
    return tuple(paragraphs)


def source_document(article_id: str, doc_id: str, url: str, body_text: str) -> SourceDocument:
    return SourceDocument(article_id=article_id, doc_id=doc_id, url=url, paragraphs=split_paragraphs(body_text))


def render_article(
    article: NormalizedArticle,
    document: SourceDocument,
    links: tuple[str, ...],
    *,
    max_chars: int,
    only: Collection[str] | None = None,
) -> tuple[str, tuple[str, ...]]:
    """The article as one untrusted block: metadata, cited links, addressed paragraphs.

    Returns the block and the ids of paragraphs left out by `max_chars`, so the
    caller reports them as unread instead of the model guessing at them.
    """
    header = [
        f"article_id: {article.article_id}",
        f"canonical_url: {escape_untrusted(article.canonical_url)}",
        f"title: {escape_untrusted(article.title)}",
        f"author: {escape_untrusted(article.author) if article.author else '-'}",
        f"published_at: {article.published_at.isoformat()}",
        "links:",
        *(f"- {escape_untrusted(url)}" for url in links),
        *(["- なし"] if not links else []),
    ]
    return _block(header, document, max_chars=max_chars, only=only)


def render_reference(document: SourceDocument, *, max_chars: int, only: Collection[str] | None = None) -> tuple[str, tuple[str, ...]]:
    """A page the article links to, as its own untrusted block."""
    return _block([f"url: {escape_untrusted(document.url)}"], document, max_chars=max_chars, only=only)


def _block(
    header: list[str], document: SourceDocument, *, max_chars: int, only: Collection[str] | None
) -> tuple[str, tuple[str, ...]]:
    lines = [UNTRUSTED_OPEN, HEADER, f"document: {document.doc_id}", *header, "paragraphs:"]
    used = 0
    omitted: list[str] = []
    for paragraph in document.paragraphs:
        if only is not None and paragraph.paragraph_id not in only:
            continue
        if used + len(paragraph.text) > max_chars:
            omitted.append(paragraph.paragraph_id)
            continue
        used += len(paragraph.text)
        lines.append(f"[{document.doc_id}/{paragraph.paragraph_id}] {escape_untrusted(paragraph.text)}")
    lines.append(UNTRUSTED_CLOSE)
    return "\n".join(lines), tuple(omitted)


def _is_heading(text: str) -> bool:
    marker, _, rest = text.partition(" ")
    return 1 <= len(marker) <= 6 and set(marker) == {"#"} and bool(rest.strip()) and "\n" not in text


def _fence_closed(chunk: str) -> bool:
    """True when a chunk that opens a fence also closes it."""
    return len(chunk.rstrip()) > len(_FENCE) and chunk.rstrip().endswith(_FENCE)
