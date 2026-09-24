"""Research contracts: the source text addressed by paragraph, and the Evidence Packet built from it.

Three layers (#12):

1. Metadata: on `EvidencePacket` itself (URL, title, author, date, source)
2. Source Document: `SourceDocument`, the normalized text split into addressed
   paragraphs. It lives only for the duration of a run and is never persisted
3. Evidence Map: `Evidence`, `Claim`, `Concept`, `Limitation`, each pointing
   back into a Source Document through an Evidence ID

An Evidence ID names its own location, `<article_id>#<doc>/<paragraph>#<n>`,
so resolving it needs no index and cannot drift from the text: `parse_evidence_id`
splits it and `SourceDocument.paragraph` finds the paragraph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Annotated

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from ._base import ArticleId, ContractModel, HttpUrlStr, NonBlankStr, NonNegativeInt, SourceId, first_duplicate
from .articles import SourceKind

EVIDENCE_QUOTE_MAX_CHARS = 300
"""A quote is a pointer into the source, not a copy of it."""

DocId = Annotated[str, StringConstraints(pattern=r"^(?:main|ref[1-9])$")]
"""`main` is the article; `ref1`..`ref9` are primary sources it links to."""

ParagraphId = Annotated[str, StringConstraints(pattern=r"^p[1-9][0-9]{0,4}$")]

_EVIDENCE_ID = re.compile(
    r"^(?P<article>[A-Za-z0-9][A-Za-z0-9_.-]{0,63})#(?P<doc>main|ref[1-9])/(?P<paragraph>p[1-9][0-9]{0,4})#(?P<n>[1-9][0-9]{0,2})$"
)

EvidenceId = Annotated[str, StringConstraints(pattern=_EVIDENCE_ID.pattern)]
"""`<article_id>#<doc>/<paragraph>#<n>`. Fits `EvidenceRef` (at most 128 characters)."""

LocalId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,31}$")]


@dataclass(frozen=True)
class EvidenceLocation:
    article_id: str
    doc_id: str
    paragraph_id: str
    ordinal: int


def parse_evidence_id(evidence_id: str) -> EvidenceLocation:
    """Split an Evidence ID into its location. Raises `ValueError` for any other string."""
    match = _EVIDENCE_ID.match(evidence_id)
    if match is None:
        raise ValueError("not an evidence id")
    return EvidenceLocation(match["article"], match["doc"], match["paragraph"], int(match["n"]))


def make_evidence_id(article_id: str, doc_id: str, paragraph_id: str, ordinal: int) -> str:
    evidence_id = f"{article_id}#{doc_id}/{paragraph_id}#{ordinal}"
    parse_evidence_id(evidence_id)
    return evidence_id


class Paragraph(ContractModel):
    paragraph_id: ParagraphId
    section: NonBlankStr | None = None
    """The nearest heading above the paragraph, when there is one."""
    text: NonBlankStr


class SourceDocument(ContractModel):
    """Normalized text split into addressed paragraphs. Never written to Git."""

    article_id: ArticleId
    doc_id: DocId
    url: HttpUrlStr
    paragraphs: tuple[Paragraph, ...] = Field(min_length=1)

    def paragraph(self, paragraph_id: str) -> Paragraph:
        for paragraph in self.paragraphs:
            if paragraph.paragraph_id == paragraph_id:
                return paragraph
        raise KeyError(paragraph_id)

    @property
    def char_count(self) -> int:
        return sum(len(p.text) for p in self.paragraphs)

    @model_validator(mode="after")
    def _check_ids(self) -> SourceDocument:
        expected = [f"p{n}" for n in range(1, len(self.paragraphs) + 1)]
        if [p.paragraph_id for p in self.paragraphs] != expected:
            raise ValueError("paragraph ids must count up from p1 without gaps")
        return self


class EvidenceKind(str, Enum):
    STATEMENT = "statement"
    """A sentence of the source that states what was done or found."""
    CODE = "code"
    CONFIG = "config"
    NUMBER = "number"
    COMPARISON = "comparison"
    FAILURE = "failure"
    PROCEDURE = "procedure"


CONCRETE_EVIDENCE_KINDS = frozenset(EvidenceKind) - {EvidenceKind.STATEMENT}
"""What the 根拠 line of a digest entry can rest on (Design Doc, Output contract)."""

REPRODUCIBILITY_EVIDENCE_KINDS = frozenset({EvidenceKind.CODE, EvidenceKind.CONFIG, EvidenceKind.PROCEDURE})


class Evidence(ContractModel):
    evidence_id: EvidenceId
    kind: EvidenceKind
    quote: Annotated[NonBlankStr, StringConstraints(max_length=EVIDENCE_QUOTE_MAX_CHARS)]
    """Verbatim from the paragraph the ID names, as the source wrote it. Research checks it before accepting."""


class ConceptArea(str, Enum):
    CONTROL_LOOP = "control_loop"
    """Control loop・タスク分解"""
    CONTEXT_ENGINEERING = "context_engineering"
    TOOL_USE = "tool_use"
    """Tool use・権限境界"""
    STATE_MEMORY = "state_memory"
    VERIFICATION_EVAL = "verification_eval"
    FAILURE_RECOVERY = "failure_recovery"
    """Failure recovery・observability"""
    HUMAN_COLLABORATION = "human_collaboration"
    COST_LATENCY = "cost_latency"
    """Cost・latency・scaling"""


class ClaimKind(str, Enum):
    WHAT_HAPPENED = "what_happened"
    """What the authors did or observed: the 何をしたか／何が分かったか line."""
    FINDING = "finding"
    """A conclusion, recommendation or result drawn from it."""


class Claim(ContractModel):
    claim_id: LocalId
    kind: ClaimKind
    text: NonBlankStr
    evidence_ids: tuple[EvidenceId, ...] = Field(min_length=1)
    numeric: bool = False
    """The claim rests on a number, so the measurement condition has to be confirmed."""
    condition_evidence_ids: tuple[EvidenceId, ...] = ()
    """Evidence for how the number was measured or compared."""
    reproducible: bool = False
    """The source claims others can reproduce it, so code, config or steps have to be found."""
    concept_areas: tuple[ConceptArea, ...] = ()


class Concept(ContractModel):
    name: NonBlankStr
    area: ConceptArea
    evidence_ids: tuple[EvidenceId, ...] = ()


class Limitation(ContractModel):
    """A constraint the source states, or something it leaves unconfirmed. May have no evidence."""

    text: NonBlankStr
    evidence_ids: tuple[EvidenceId, ...] = ()


class ResearchStatus(str, Enum):
    COMPLETE = "complete"
    """Every sufficiency condition holds."""
    PARTIAL = "partial"
    """What happened and a concrete piece of evidence are confirmed; something else is not."""
    INSUFFICIENT = "insufficient"
    """The core conditions do not hold. Research does not relax them to get here."""


class ResearchStopReason(str, Enum):
    SUFFICIENT = "sufficient"
    NO_OPEN_QUESTIONS = "no_open_questions"
    """Evidence is missing and the source offers nothing further to check."""
    BUDGET_EXHAUSTED = "budget_exhausted"
    """Questions remained when a round, page or time limit was reached."""
    EXTRACTION_FAILED = "extraction_failed"
    """The model's Evidence Map never passed the reference checks."""
    NOT_RESEARCHED = "not_researched"
    """Another article of the same cluster was researched instead."""


class ResearchBudget(ContractModel):
    """Limits Python enforces. The model can ask for more; it never gets it.

    The defaults are #12's initial stop conditions. `config/config.json` may
    change them within these ranges; nothing a model returns can. Following
    links beyond the first hop is not implemented, so the depth stays at most 1.
    """

    max_rounds: int = Field(default=2, ge=1, le=5, strict=True)
    max_extra_pages: int = Field(default=3, ge=0, le=10, strict=True)
    """Pages other than the article itself."""
    max_link_depth: int = Field(default=1, ge=0, le=1, strict=True)
    max_open_questions: int = Field(default=3, ge=0, le=10, strict=True)
    max_seconds: float = Field(default=240.0, gt=0, le=1800)
    max_source_chars: int = Field(default=40_000, ge=1_000, le=200_000, strict=True)
    """Characters of one document given to the model. The rest is reported as unread."""


class FetchedReference(ContractModel):
    """One extra page research tried to read."""

    url: HttpUrlStr
    doc_id: DocId | None = None
    """Set when the page was read and became a Source Document."""
    failure: NonBlankStr | None = None

    @model_validator(mode="after")
    def _check(self) -> FetchedReference:
        if (self.doc_id is None) == (self.failure is None):
            raise ValueError("a reference is either read (doc_id) or failed (failure)")
        return self


class ResearchTrace(ContractModel):
    rounds: NonNegativeInt
    extra_pages: tuple[FetchedReference, ...] = ()
    paragraph_requests: NonNegativeInt = 0
    """Paragraphs of an already read document that were sent again to answer a question."""
    elapsed_ms: NonNegativeInt


class EvidencePacket(ContractModel):
    """What the Selector reads about one article instead of its full text."""

    article_id: ArticleId
    source_id: SourceId
    source_kind: SourceKind
    canonical_url: HttpUrlStr
    title: NonBlankStr
    author: NonBlankStr | None = None
    published_at: AwareDatetime
    represented_by: ArticleId | None = None
    """For a supporting article of a cluster: the article whose packet carries the research."""
    supporting_ids: tuple[ArticleId, ...] = ()
    """For a representative: the other articles of its cluster."""
    concepts: tuple[Concept, ...] = ()
    claims: tuple[Claim, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    limitations: tuple[Limitation, ...] = ()
    unresolved: tuple[NonBlankStr, ...] = ()
    """未確認事項 and fetch failures, stated rather than filled in."""
    status: ResearchStatus
    stop_reason: ResearchStopReason
    trace: ResearchTrace

    @property
    def evidence_ids(self) -> frozenset[str]:
        return frozenset(e.evidence_id for e in self.evidence)

    def evidence_by_id(self, evidence_id: str) -> Evidence:
        for evidence in self.evidence:
            if evidence.evidence_id == evidence_id:
                return evidence
        raise KeyError(evidence_id)

    @model_validator(mode="after")
    def _check_references(self) -> EvidencePacket:
        ids = [e.evidence_id for e in self.evidence]
        if first_duplicate(ids) is not None:
            raise ValueError("evidence ids must be unique")
        for evidence_id in ids:
            if parse_evidence_id(evidence_id).article_id != self.article_id:
                raise ValueError("evidence must belong to this article")
        known = set(ids)
        referenced = [
            *(i for c in self.claims for i in (*c.evidence_ids, *c.condition_evidence_ids)),
            *(i for c in self.concepts for i in c.evidence_ids),
            *(i for l in self.limitations for i in l.evidence_ids),
        ]
        if not known.issuperset(referenced):
            raise ValueError("a claim, concept or limitation references unknown evidence")
        if first_duplicate(c.claim_id for c in self.claims) is not None:
            raise ValueError("claim ids must be unique")
        if self.represented_by is not None:
            if self.represented_by == self.article_id or self.claims or self.evidence or self.supporting_ids:
                raise ValueError("a supporting article carries no research of its own")
            if self.stop_reason is not ResearchStopReason.NOT_RESEARCHED:
                raise ValueError("a supporting article is not researched")
            if self.status is not ResearchStatus.INSUFFICIENT:
                raise ValueError("a supporting article has no evidence of its own")
        elif self.stop_reason is ResearchStopReason.NOT_RESEARCHED:
            raise ValueError("only a supporting article can be left unresearched")
        if self.status is ResearchStatus.COMPLETE and self.stop_reason is not ResearchStopReason.SUFFICIENT:
            raise ValueError("a complete packet stops because it is sufficient")
        return self
