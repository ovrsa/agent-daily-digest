"""Judge report: audit findings with the assessment kept apart from the fix."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from ._base import ArticleId, ContractModel, NonBlankStr, first_duplicate

BOUNDARY_EXCLUDED_AUDIT_MAX = 5
"""Excluded articles near the boundary that the Judge audits, at most."""

SOURCE_EVIDENCE_MAX_CHARS = 300
"""Keeps quotations short enough for a commit comment; long reposts are not allowed."""

FindingId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")]


class AuditTargetKind(str, Enum):
    """Kind of audit target, used by #8 when it selects the articles the Judge audits."""

    MUST_READ = "must_read"
    WORTH_KNOWING = "worth_knowing"
    BOUNDARY_EXCLUDED = "boundary_excluded"
    DUPLICATE_REPRESENTATIVE = "duplicate_representative"


class AuditCategory(str, Enum):
    SCOPE_FIT = "scope_fit"
    """対象適合性"""
    NOVELTY = "novelty"
    """新規性"""
    PRACTICALITY = "practicality"
    """実用性"""
    SPECIFICITY = "specificity"
    """具体性"""
    GROUNDEDNESS = "groundedness"
    """根拠性"""
    SOURCE_RELIABILITY = "source_reliability"
    """情報源の信頼性"""
    SUMMARY_FAITHFULNESS = "summary_faithfulness"
    """要約忠実性"""
    RECOMMENDATION_VALIDITY = "recommendation_validity"
    """推薦妥当性"""
    DUPLICATION = "duplication"
    """重複性"""
    EXCLUSION_VALIDITY = "exclusion_validity"
    """除外妥当性"""


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingAssessment(ContractModel):
    """What is wrong and how sure the Judge is. Written before any fix is proposed."""

    category: AuditCategory
    severity: Severity
    problem: NonBlankStr = Field(description="問題")
    source_evidence: Annotated[
        NonBlankStr, StringConstraints(max_length=SOURCE_EVIDENCE_MAX_CHARS)
    ] = Field(description="本文上の根拠。短い引用または位置のみ")
    confidence: Confidence


class ImprovementCandidate(ContractModel):
    """A proposed fix. It must not feed back into the assessment."""

    suggested_fix: NonBlankStr = Field(description="修正案")


class JudgeFinding(ContractModel):
    # Field order is also generation order for structured output: the assessment
    # comes first so a persuasive fix cannot shape the severity or confidence.
    finding_id: FindingId
    article_id: ArticleId
    assessment: FindingAssessment
    improvement: ImprovementCandidate

    def to_metrics(self) -> FindingMetrics:
        return FindingMetrics(
            finding_id=self.finding_id,
            article_id=self.article_id,
            category=self.assessment.category,
            severity=self.assessment.severity,
            confidence=self.assessment.confidence,
        )


class JudgeReport(ContractModel):
    """Structured output of the Judge. Zero findings is a valid report."""

    findings: tuple[JudgeFinding, ...] = ()

    @model_validator(mode="after")
    def _check_ids(self) -> JudgeReport:
        if first_duplicate(finding.finding_id for finding in self.findings) is not None:
            raise ValueError("finding_id must be unique")
        return self


class FindingMetrics(ContractModel):
    """Finding classification kept in run metrics, without the finding text."""

    finding_id: FindingId
    article_id: ArticleId
    category: AuditCategory
    severity: Severity
    confidence: Confidence
