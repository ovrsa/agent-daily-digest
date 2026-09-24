"""Which articles the Judge audits, chosen by fixed rules (Design Doc, Judge).

- every Must Read and every Worth Knowing article
- the five excluded articles closest to the boundary
- the representative of every duplicate group

The boundary score is the sum of the six axes, with ties broken by the value
of reading the original, then reader impact, then article id. It only picks
which excluded articles are worth a second look; it never decides adoption,
which the Selector does without a total (#6). Articles excluded as the
non-representative members of a duplicate group are left to the audit of
their representative.
"""

from __future__ import annotations

from dataclasses import dataclass

from digest_contracts import BOUNDARY_EXCLUDED_AUDIT_MAX, AuditTargetKind, AxisScores, ExcludedArticle, SelectorOutput


@dataclass(frozen=True)
class AuditTarget:
    article_id: str
    kinds: tuple[AuditTargetKind, ...]


def boundary_score(scores: AxisScores) -> tuple[int, int, int]:
    total = (
        scores.practicality
        + scores.specificity_reproducibility
        + scores.novelty
        + scores.source_reliability
        + scores.reader_impact
        + scores.read_original_value
    )
    return (total, scores.read_original_value, scores.reader_impact)


def select_targets(output: SelectorOutput) -> tuple[AuditTarget, ...]:
    kinds: dict[str, list[AuditTargetKind]] = {}

    def add(article_id: str, kind: AuditTargetKind) -> None:
        kinds.setdefault(article_id, [])
        if kind not in kinds[article_id]:
            kinds[article_id].append(kind)

    for article in output.must_read:
        add(article.article_id, AuditTargetKind.MUST_READ)
    for article in output.worth_knowing:
        add(article.article_id, AuditTargetKind.WORTH_KNOWING)
    grouped = {i for group in output.duplicate_groups for i in group.duplicate_ids}
    candidates = [a for a in output.excluded if a.article_id not in grouped]
    for article in sorted(candidates, key=_boundary_key)[:BOUNDARY_EXCLUDED_AUDIT_MAX]:
        add(article.article_id, AuditTargetKind.BOUNDARY_EXCLUDED)
    for group in output.duplicate_groups:
        add(group.representative_id, AuditTargetKind.DUPLICATE_REPRESENTATIVE)
    return tuple(AuditTarget(article_id, tuple(k)) for article_id, k in kinds.items())


def _boundary_key(article: ExcludedArticle) -> tuple[int, int, int, str]:
    total, original, impact = boundary_score(article.scores)
    return (-total, -original, -impact, article.article_id)
