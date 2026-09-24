"""The Evidence Map a research round returns, and the checks it has to pass.

The model writes local ids (`e1`, `c1`) and points at paragraphs by address. It
never mints an Evidence ID: `accept` assigns one only after checking that the
address exists and the quote is really in that paragraph. A map that fails any
check is rejected whole with `OutputRejected`, which `measured_call` retries
within its policy; when the retries run out, that is the extraction-failure
path of the Execution Graph.

A kind label is checked too, because sufficiency counts on it: a quote
labelled `number` or `comparison` has to contain a digit, and one labelled
`code` or `config` has to come from a code block or inline code. A label that
fails is lowered to `statement` rather than rejected; the quote itself is still
real. The first real-model run labelled "We compared two retry policies." a
comparison, which made an article with no numbers look fully supported.

The schema avoids `oneOf` and discriminators: the #2 spike confirmed that
structured output enforces `anyOf`, `$ref`, `pattern` and length limits, and
nothing else is relied on.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from digest_contracts import (
    EVIDENCE_QUOTE_MAX_CHARS,
    Claim,
    ClaimKind,
    Concept,
    ConceptArea,
    Evidence,
    EvidenceKind,
    Limitation,
    SourceDocument,
    ValidationIssue,
    make_evidence_id,
    parse_evidence_id,
)
from digest_observe import OutputRejected

MAX_EVIDENCE = 24
MAX_CLAIMS = 10
MAX_CONCEPTS = 8
MAX_LIMITATIONS = 6
MAX_QUESTIONS = 5
"""The schema lets the model propose five questions; `ResearchBudget` decides how many are acted on."""

Local = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,31}$")]
Ref = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^\S+$")]
"""A local id from this round, or a full Evidence ID from an earlier one."""
Sentence = Annotated[str, StringConstraints(min_length=1, max_length=600, pattern=r"\S")]
DocAddress = Annotated[str, StringConstraints(pattern=r"^(?:main|ref[1-9])$")]
ParagraphAddress = Annotated[str, StringConstraints(pattern=r"^p[1-9][0-9]{0,4}$")]


class _Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceDraft(_Draft):
    id: Local
    doc: DocAddress
    paragraph: ParagraphAddress
    kind: EvidenceKind
    quote: Annotated[str, StringConstraints(min_length=1, max_length=EVIDENCE_QUOTE_MAX_CHARS, pattern=r"\S")]


class ClaimDraft(_Draft):
    id: Local
    kind: ClaimKind
    text: Sentence
    evidence: tuple[Ref, ...] = Field(min_length=1, max_length=6)
    numeric: bool
    conditions: tuple[Ref, ...] = Field(default=(), max_length=6)
    reproducible: bool
    areas: tuple[ConceptArea, ...] = Field(default=(), max_length=4)


class ConceptDraft(_Draft):
    name: Annotated[str, StringConstraints(min_length=1, max_length=80, pattern=r"\S")]
    area: ConceptArea
    evidence: tuple[Ref, ...] = Field(default=(), max_length=6)


class LimitationDraft(_Draft):
    text: Sentence
    evidence: tuple[Ref, ...] = Field(default=(), max_length=6)


class QuestionDraft(_Draft):
    """An unresolved point and where to look: a URL the article links to, or paragraphs."""

    question: Sentence
    url: Annotated[str, StringConstraints(min_length=1, max_length=2048)] | None = None
    doc: DocAddress | None = None
    paragraphs: tuple[ParagraphAddress, ...] = Field(default=(), max_length=6)


class ExtractionOutput(_Draft):
    """Structured output of one research round."""

    evidence: tuple[EvidenceDraft, ...] = Field(default=(), max_length=MAX_EVIDENCE)
    claims: tuple[ClaimDraft, ...] = Field(default=(), max_length=MAX_CLAIMS)
    concepts: tuple[ConceptDraft, ...] = Field(default=(), max_length=MAX_CONCEPTS)
    limitations: tuple[LimitationDraft, ...] = Field(default=(), max_length=MAX_LIMITATIONS)
    open_questions: tuple[QuestionDraft, ...] = Field(default=(), max_length=MAX_QUESTIONS)


@dataclass(frozen=True)
class EvidenceMap:
    evidence: tuple[Evidence, ...] = ()
    claims: tuple[Claim, ...] = ()
    concepts: tuple[Concept, ...] = ()
    limitations: tuple[Limitation, ...] = ()
    questions: tuple[QuestionDraft, ...] = ()


def accept(output: ExtractionOutput, documents: Mapping[str, SourceDocument], *, prior: EvidenceMap | None = None) -> EvidenceMap:
    """Check every reference and assign Evidence IDs, or raise `OutputRejected`.

    With `prior`, the round adds to an earlier map: earlier evidence keeps its
    ids and may be cited by them, and a claim with an earlier claim's id
    replaces it.
    """
    prior = prior or EvidenceMap()
    issues: list[ValidationIssue] = []
    article_id = next(iter(documents.values())).article_id

    evidence = list(prior.evidence)
    known_ids = {e.evidence_id for e in evidence}
    by_quote = {(_address(e.evidence_id), _collapse(e.quote)): e.evidence_id for e in evidence}
    ordinals: dict[str, int] = {}
    for evidence_id in known_ids:
        location = parse_evidence_id(evidence_id)
        key = f"{location.doc_id}/{location.paragraph_id}"
        ordinals[key] = max(ordinals.get(key, 0), location.ordinal)

    local: dict[str, str] = {}
    for index, draft in enumerate(output.evidence):
        at = f"evidence.{index}"
        if draft.id in local:
            issues.append(ValidationIssue(loc=f"{at}.id", type="duplicate_local_id"))
            continue
        document = documents.get(draft.doc)
        if document is None:
            issues.append(ValidationIssue(loc=f"{at}.doc", type="unknown_document"))
            continue
        try:
            paragraph = document.paragraph(draft.paragraph)
        except KeyError:
            issues.append(ValidationIssue(loc=f"{at}.paragraph", type="unknown_paragraph"))
            continue
        quote = _collapse(draft.quote)
        if quote not in _collapse(paragraph.text):
            issues.append(ValidationIssue(loc=f"{at}.quote", type="quote_not_in_paragraph"))
            continue
        kind = _checked_kind(draft.kind, draft.quote, paragraph.text)
        address = f"{draft.doc}/{draft.paragraph}"
        existing = by_quote.get((address, quote))
        if existing is None:
            ordinals[address] = ordinals.get(address, 0) + 1
            existing = make_evidence_id(article_id, draft.doc, draft.paragraph, ordinals[address])
            by_quote[(address, quote)] = existing
            known_ids.add(existing)
            evidence.append(Evidence(evidence_id=existing, kind=kind, quote=draft.quote.strip()))
        local[draft.id] = existing

    def resolve(refs: tuple[str, ...], at: str) -> tuple[str, ...]:
        resolved: list[str] = []
        for position, ref in enumerate(refs):
            target = local.get(ref) or (ref if ref in known_ids else None)
            if target is None:
                issues.append(ValidationIssue(loc=f"{at}.{position}", type="unknown_evidence"))
            elif target not in resolved:
                resolved.append(target)
        return tuple(resolved)

    claims = {claim.claim_id: claim for claim in prior.claims}
    seen_claims: set[str] = set()
    for index, draft in enumerate(output.claims):
        if draft.id in seen_claims:
            issues.append(ValidationIssue(loc=f"claims.{index}.id", type="duplicate_local_id"))
            continue
        seen_claims.add(draft.id)
        supporting = resolve(draft.evidence, f"claims.{index}.evidence")
        conditions = resolve(draft.conditions, f"claims.{index}.conditions")
        if supporting:
            claims[draft.id] = Claim(
                claim_id=draft.id,
                kind=draft.kind,
                text=draft.text,
                evidence_ids=supporting,
                numeric=draft.numeric,
                condition_evidence_ids=conditions,
                reproducible=draft.reproducible,
                concept_areas=tuple(dict.fromkeys(draft.areas)),
            )
    concepts = [
        Concept(name=d.name, area=d.area, evidence_ids=resolve(d.evidence, f"concepts.{i}.evidence"))
        for i, d in enumerate(output.concepts)
    ]
    limitations = [
        Limitation(text=d.text, evidence_ids=resolve(d.evidence, f"limitations.{i}.evidence"))
        for i, d in enumerate(output.limitations)
    ]
    if issues:
        raise OutputRejected(tuple(issues))
    return EvidenceMap(
        evidence=tuple(evidence),
        claims=tuple(claims.values()),
        concepts=(*prior.concepts, *concepts),
        limitations=(*prior.limitations, *limitations),
        questions=output.open_questions,
    )


_NUMERIC_KINDS = frozenset({EvidenceKind.NUMBER, EvidenceKind.COMPARISON})
_CODE_KINDS = frozenset({EvidenceKind.CODE, EvidenceKind.CONFIG})


def _checked_kind(kind: EvidenceKind, quote: str, paragraph: str) -> EvidenceKind:
    if kind in _NUMERIC_KINDS and not any(ch.isdigit() for ch in quote):
        return EvidenceKind.STATEMENT
    if kind in _CODE_KINDS and not (paragraph.startswith("```") or "`" in quote):
        return EvidenceKind.STATEMENT
    return kind


def _address(evidence_id: str) -> str:
    location = parse_evidence_id(evidence_id)
    return f"{location.doc_id}/{location.paragraph_id}"


def _collapse(text: str) -> str:
    return " ".join(text.split())
