"""Addressed paragraphs, and the untrusted blocks a model reads them in.

`agent_daily_digest.normalize` joins the blocks it keeps with a blank line, glues list items
and table rows with a single newline, and fences code with three backticks. A
paragraph here is one of those blocks: splitting on blank lines recovers them,
except inside a fenced code block, whose own blank lines belong to the code.

The split is a pure function of the body text, so the same body always yields
the same paragraph ids and an Evidence ID resolves the same way on every run.

Everything a page wrote - title, author, links, paragraphs - reaches the model
inside one delimited block, through `escape_untrusted`, the same boundary
`agent_daily_digest.content.text.as_untrusted_block` uses.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence

from agent_daily_digest.content.text import (
    HEADER,
    UNTRUSTED_CLOSE,
    UNTRUSTED_OPEN,
    escape_untrusted,
)
from agent_daily_digest.contracts.articles import NormalizedArticle
from agent_daily_digest.contracts.research import (
    Claim,
    Concept,
    Evidence,
    EvidencePacket,
    Limitation,
    Paragraph,
    SourceDocument,
    parse_evidence_id,
)
from agent_daily_digest.research.extraction import EvidenceMap

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


def render_reference(
    document: SourceDocument, *, max_chars: int, only: Collection[str] | None = None
) -> tuple[str, tuple[str, ...]]:
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


class SourceLibrary:
    def __init__(self) -> None:
        self._documents: dict[tuple[str, str], SourceDocument] = {}

    def add(self, document: SourceDocument) -> None:
        self._documents[(document.article_id, document.doc_id)] = document

    def document(self, article_id: str, doc_id: str) -> SourceDocument:
        return self._documents[(article_id, doc_id)]

    def resolve(self, evidence_id: str) -> Paragraph:
        """The paragraph an Evidence ID names. Raises `KeyError` when it is not in the library."""
        location = parse_evidence_id(evidence_id)
        return self.document(location.article_id, location.doc_id).paragraph(location.paragraph_id)

    def excerpt(self, evidence_ids: Iterable[str], *, max_chars: int = 4_000) -> str:
        """The paragraphs behind `evidence_ids`, each once, as one untrusted block.

        An ID is model-written text: one that is not an Evidence ID, or names
        a paragraph the library does not hold, is reported as not found, and
        every ID is escaped like the rest of the block.
        """
        lines = [UNTRUSTED_OPEN, HEADER]
        used = 0
        seen: set[tuple[str, str, str]] = set()
        for evidence_id in evidence_ids:
            label = escape_untrusted(evidence_id)
            try:
                location = parse_evidence_id(evidence_id)
            except ValueError:
                lines.append(f"[{label}] (原文が見つからない)")
                continue
            key = (location.article_id, location.doc_id, location.paragraph_id)
            if key in seen:
                continue
            seen.add(key)
            try:
                paragraph = self.resolve(evidence_id)
            except KeyError:
                lines.append(f"[{label}] (原文が見つからない)")
                continue
            if used + len(paragraph.text) > max_chars:
                lines.append(f"[{label}] (文字数の上限で省略)")
                continue
            used += len(paragraph.text)
            lines.append(f"[{label}] {escape_untrusted(paragraph.text)}")
        lines.append(UNTRUSTED_CLOSE)
        return "\n".join(lines)


def render_packet(packet: EvidencePacket) -> str:
    """One packet as text for a model. Quotes only; no paragraph is copied whole."""
    lines = [
        UNTRUSTED_OPEN,
        HEADER,
        f"article_id: {packet.article_id}",
        f"source: {packet.source_id} ({packet.source_kind.value})",
        f"canonical_url: {escape_untrusted(packet.canonical_url)}",
        f"title: {escape_untrusted(packet.title)}",
        f"author: {escape_untrusted(packet.author) if packet.author else '-'}",
        f"published_at: {packet.published_at.isoformat()}",
        f"research: {packet.status.value} ({packet.stop_reason.value})",
    ]
    if packet.represented_by is not None:
        lines.append(f"represented_by: {packet.represented_by}")
    if packet.supporting_ids:
        lines.append(f"supporting: {', '.join(packet.supporting_ids)}")
    lines += _map_lines(packet.evidence, packet.claims, packet.concepts, packet.limitations)
    if packet.unresolved:
        lines.append("unresolved:")
        lines += [f"- {escape_untrusted(item)}" for item in packet.unresolved]
    lines.append(UNTRUSTED_CLOSE)
    return "\n".join(lines)


def render_map(evidence_map: EvidenceMap) -> str:
    """An Evidence Map in progress, for the next research round."""
    return "\n".join(
        [
            UNTRUSTED_OPEN,
            HEADER,
            *_map_lines(evidence_map.evidence, evidence_map.claims, evidence_map.concepts, evidence_map.limitations),
            UNTRUSTED_CLOSE,
        ]
    )


def _map_lines(
    evidence: Sequence[Evidence],
    claims: Sequence[Claim],
    concepts: Sequence[Concept],
    limitations: Sequence[Limitation],
) -> list[str]:
    lines: list[str] = []
    if claims:
        lines.append("claims:")
        for claim in claims:
            flags = [flag for flag, on in (("numeric", claim.numeric), ("reproducible", claim.reproducible)) if on]
            conditions = (
                f" conditions: {', '.join(claim.condition_evidence_ids)}" if claim.condition_evidence_ids else ""
            )
            lines.append(
                f"- {claim.claim_id} ({claim.kind.value}{', ' + ', '.join(flags) if flags else ''}) "
                f"{escape_untrusted(claim.text)} evidence: {', '.join(claim.evidence_ids)}{conditions}"
            )
    if evidence:
        lines.append("evidence:")
        lines += [f"- {e.evidence_id} [{e.kind.value}] {escape_untrusted(e.quote)}" for e in evidence]
    if concepts:
        lines.append("concepts:")
        lines += [f"- {escape_untrusted(c.name)} ({c.area.value})" for c in concepts]
    if limitations:
        lines.append("limitations:")
        lines += [f"- {escape_untrusted(item.text)}" for item in limitations]
    return lines
