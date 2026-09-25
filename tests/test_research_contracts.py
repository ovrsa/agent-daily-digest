"""Research contracts: Evidence IDs that resolve by themselves, and packets whose references hold."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_daily_digest.contracts import (
    EvidencePacket,
    ResearchBudget,
    SourceDocument,
    make_evidence_id,
    parse_evidence_id,
)
from research_support import PUBLISHED


def packet(**overrides) -> dict:
    data = {
        "article_id": "a001",
        "source_id": "simonw",
        "source_kind": "fixed_watch",
        "canonical_url": "https://example.com/posts/a001",
        "title": "Title",
        "published_at": PUBLISHED.isoformat(),
        "claims": [{"claim_id": "c1", "kind": "what_happened", "text": "t", "evidence_ids": ["a001#main/p2#1"]}],
        "evidence": [{"evidence_id": "a001#main/p2#1", "kind": "code", "quote": "q"}],
        "status": "complete",
        "stop_reason": "sufficient",
        "trace": {"rounds": 1, "elapsed_ms": 10},
    }
    data.update(overrides)
    return data


def test_an_evidence_id_names_its_own_location() -> None:
    evidence_id = make_evidence_id("a-1.x", "ref2", "p10", 3)
    assert evidence_id == "a-1.x#ref2/p10#3"
    location = parse_evidence_id(evidence_id)
    assert (location.article_id, location.doc_id, location.paragraph_id, location.ordinal) == ("a-1.x", "ref2", "p10", 3)


@pytest.mark.parametrize("bad", ["a001", "a001#main/p0#1", "a001#other/p1#1", "a001#main/p1#0", "a 1#main/p1#1", "a001#main/p1#1 "])
def test_anything_else_is_not_an_evidence_id(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_evidence_id(bad)


def test_a_valid_packet_validates() -> None:
    assert EvidencePacket.model_validate(packet()).evidence_ids == {"a001#main/p2#1"}


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"claims": [{"claim_id": "c1", "kind": "finding", "text": "t", "evidence_ids": ["a001#main/p9#1"]}]}, "unknown evidence"),
        ({"evidence": [{"evidence_id": "b002#main/p2#1", "kind": "code", "quote": "q"}], "claims": []}, "another article"),
        ({"evidence": [{"evidence_id": "a001#main/p2#1", "kind": "code", "quote": "q"}] * 2}, "duplicate"),
        ({"status": "complete", "stop_reason": "budget_exhausted"}, "complete needs sufficient"),
        ({"stop_reason": "not_researched", "status": "insufficient"}, "only supporting articles are unresearched"),
        ({"represented_by": "a002", "status": "insufficient", "stop_reason": "not_researched"}, "supporting carries research"),
    ],
)
def test_a_packet_with_broken_references_is_rejected(overrides: dict, reason: str) -> None:
    with pytest.raises(ValidationError):
        EvidencePacket.model_validate(packet(**overrides))


def test_a_supporting_packet_carries_nothing_but_the_pointer() -> None:
    supporting = packet(represented_by="a002", claims=[], evidence=[], status="insufficient", stop_reason="not_researched")
    assert EvidencePacket.model_validate(supporting).represented_by == "a002"


def test_the_default_budget_is_the_initial_stop_condition() -> None:
    budget = ResearchBudget()
    assert (budget.max_rounds, budget.max_extra_pages, budget.max_link_depth, budget.max_open_questions) == (2, 3, 1, 3)


@pytest.mark.parametrize("field", ["max_rounds", "max_extra_pages", "max_open_questions"])
def test_the_budget_rejects_a_value_outside_its_range(field: str) -> None:
    with pytest.raises(ValidationError):
        ResearchBudget.model_validate({field: 99})
    with pytest.raises(ValidationError):
        ResearchBudget.model_validate({"max_link_depth": 2})


def test_paragraph_ids_count_up_without_gaps() -> None:
    with pytest.raises(ValidationError):
        SourceDocument.model_validate(
            {"article_id": "a001", "doc_id": "main", "url": "https://example.com/", "paragraphs": [{"paragraph_id": "p2", "text": "x"}]}
        )
