"""Looking up the source behind an Evidence ID, and the packet text later stages read.

The Selector and the Judge read `render_packet`, which carries claims and short
quotes but never a document: that is how the Selector's ordinary input stays
free of full text (#12, #6). When a later stage needs more than a quote, it
asks `SourceLibrary.excerpt` for the paragraphs behind specific Evidence IDs.

The library holds Source Documents in memory for one run. It has no method that
writes, and nothing else in this package opens a file.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from digest_contracts import (
    Claim,
    Concept,
    Evidence,
    EvidencePacket,
    Limitation,
    Paragraph,
    SourceDocument,
    parse_evidence_id,
)
from digest_normalize import HEADER, UNTRUSTED_CLOSE, UNTRUSTED_OPEN, escape_untrusted

from .extraction import EvidenceMap


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
        """The paragraphs behind `evidence_ids`, each once, as one untrusted block."""
        lines = [UNTRUSTED_OPEN, HEADER]
        used = 0
        seen: set[tuple[str, str, str]] = set()
        for evidence_id in evidence_ids:
            location = parse_evidence_id(evidence_id)
            key = (location.article_id, location.doc_id, location.paragraph_id)
            if key in seen:
                continue
            seen.add(key)
            try:
                paragraph = self.resolve(evidence_id)
            except KeyError:
                lines.append(f"[{evidence_id}] (原文が見つからない)")
                continue
            if used + len(paragraph.text) > max_chars:
                lines.append(f"[{evidence_id}] (文字数の上限で省略)")
                continue
            used += len(paragraph.text)
            lines.append(f"[{evidence_id}] {escape_untrusted(paragraph.text)}")
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
        [UNTRUSTED_OPEN, HEADER, *_map_lines(evidence_map.evidence, evidence_map.claims, evidence_map.concepts, evidence_map.limitations), UNTRUSTED_CLOSE]
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
            conditions = f" conditions: {', '.join(claim.condition_evidence_ids)}" if claim.condition_evidence_ids else ""
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
