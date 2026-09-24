"""Whether an Evidence Map is enough to write a digest entry from.

The conditions are the ones #12 lists, checked in Python so no model can relax
them. `assess` returns the status and every unmet condition as a sentence for
`EvidencePacket.unresolved`, so the Selector sees what is missing instead of
a gap filled with a guess.
"""

from __future__ import annotations

from dataclasses import dataclass

from digest_contracts import (
    CONCRETE_EVIDENCE_KINDS,
    REPRODUCIBILITY_EVIDENCE_KINDS,
    ClaimKind,
    NormalizedArticle,
    ResearchStatus,
)

from .extraction import EvidenceMap


@dataclass(frozen=True)
class Assessment:
    status: ResearchStatus
    unmet: tuple[str, ...]


def assess(article: NormalizedArticle, evidence_map: EvidenceMap) -> Assessment:
    kinds = {e.evidence_id: e.kind for e in evidence_map.evidence}
    cited = {i for c in evidence_map.claims for i in c.evidence_ids}

    core: list[str] = []
    if not any(c.kind is ClaimKind.WHAT_HAPPENED for c in evidence_map.claims):
        core.append("何をしたか／何が分かったかを支える根拠が見つからない")
    if not any(kinds[i] in CONCRETE_EVIDENCE_KINDS for i in cited):
        core.append("掲載の根拠に使える具体的な根拠（コード、設定、数値、比較、失敗例、手順）が見つからない")

    other: list[str] = []
    if article.author is None:
        other.append("著者を確認できない")
    for claim in evidence_map.claims:
        if claim.numeric and not claim.condition_evidence_ids:
            other.append(f"主張 {claim.claim_id} の数値の測定・比較条件を確認できない")
        if claim.reproducible and not any(kinds[i] in REPRODUCIBILITY_EVIDENCE_KINDS for i in claim.evidence_ids):
            other.append(f"主張 {claim.claim_id} の再現に必要なコード、設定、手順を確認できない")

    if core:
        return Assessment(ResearchStatus.INSUFFICIENT, (*core, *other))
    if other:
        return Assessment(ResearchStatus.PARTIAL, tuple(other))
    return Assessment(ResearchStatus.COMPLETE, ())
