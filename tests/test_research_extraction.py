"""Reference integrity: the model points, Python checks and mints the Evidence IDs."""

from __future__ import annotations

import pytest

from digest_observe import OutputRejected
from digest_research import ExtractionOutput, accept, source_document
from research_support import article, complete_map


def documents():
    basic = article()
    return {"main": source_document(basic.article_id, "main", basic.canonical_url, basic.body_text)}


def test_valid_references_become_evidence_ids_that_name_their_paragraph() -> None:
    evidence_map = accept(ExtractionOutput.model_validate(complete_map()), documents())
    assert [e.evidence_id for e in evidence_map.evidence] == ["a001#main/p2#1", "a001#main/p5#1", "a001#main/p7#1"]
    assert evidence_map.claims[0].evidence_ids == ("a001#main/p2#1", "a001#main/p5#1")
    assert evidence_map.concepts[0].evidence_ids == ("a001#main/p5#1",)


@pytest.mark.parametrize(
    ("change", "issue"),
    [
        ({"quote": "We capped agent retries at 5 per stage"}, "quote_not_in_paragraph"),
        ({"paragraph": "p99"}, "unknown_paragraph"),
        ({"doc": "ref1"}, "unknown_document"),
    ],
)
def test_a_quote_that_is_not_where_it_claims_to_be_rejects_the_map(change: dict, issue: str) -> None:
    data = complete_map()
    data["evidence"][0].update(change)
    with pytest.raises(OutputRejected) as caught:
        accept(ExtractionOutput.model_validate(data), documents())
    assert issue in {i.type for i in caught.value.issues}
    # The rejected quote is not repeated in the issues.
    assert all("5 per stage" not in i.loc for i in caught.value.issues)


def test_a_claim_citing_evidence_that_does_not_exist_rejects_the_map() -> None:
    data = complete_map()
    data["claims"][0]["evidence"] = ["e9"]
    with pytest.raises(OutputRejected) as caught:
        accept(ExtractionOutput.model_validate(data), documents())
    assert {i.type for i in caught.value.issues} == {"unknown_evidence"}


def test_the_same_quote_twice_is_one_piece_of_evidence() -> None:
    data = complete_map()
    data["evidence"].append({**data["evidence"][1], "id": "e4"})
    data["claims"][1]["evidence"] = ["e4"]
    evidence_map = accept(ExtractionOutput.model_validate(data), documents())
    assert len(evidence_map.evidence) == 3
    assert evidence_map.claims[1].evidence_ids == ("a001#main/p5#1",)


def test_a_later_round_cites_earlier_evidence_by_id_and_replaces_a_claim() -> None:
    first = accept(ExtractionOutput.model_validate(complete_map()), documents())
    second = ExtractionOutput.model_validate(
        {
            "evidence": [{"id": "e1", "doc": "main", "paragraph": "p2", "kind": "number", "quote": "measured the change"}],
            "claims": [{"id": "c2", "kind": "finding", "text": "改訂した主張。", "evidence": ["a001#main/p7#1", "e1"], "numeric": True, "conditions": ["e1"], "reproducible": False}],
        }
    )
    merged = accept(second, documents(), prior=first)
    # Earlier ids are kept; a second quote of p2 gets the next ordinal.
    assert [e.evidence_id for e in merged.evidence][-1] == "a001#main/p2#2"
    assert [c.claim_id for c in merged.claims] == ["c1", "c2"]
    assert merged.claims[1].text == "改訂した主張。"
    assert merged.claims[1].condition_evidence_ids == ("a001#main/p2#2",)


def test_the_schema_uses_only_keywords_structured_output_enforces() -> None:
    import json

    text = json.dumps(ExtractionOutput.model_json_schema())
    for keyword in ('"oneOf"', '"discriminator"', '"allOf"', '"prefixItems"'):
        assert keyword not in text


@pytest.mark.parametrize(
    ("kind", "paragraph", "quote", "expected"),
    [
        ("comparison", "p2", "measured the change", "statement"),
        ("number", "p2", "We capped agent retries at `3` per stage", "number"),
        ("code", "p8", "The full configuration lives in the repository.", "statement"),
        ("code", "p5", "runner.attach(budget)", "code"),
        ("config", "p2", "`3`", "config"),
        ("code", "p2", "We capped agent retries at `3` per stage", "statement"),
    ],
)
def test_a_kind_label_the_quote_cannot_support_is_lowered_to_statement(kind, paragraph, quote, expected) -> None:
    data = {"evidence": [{"id": "e1", "doc": "main", "paragraph": paragraph, "kind": kind, "quote": quote}]}
    (evidence,) = accept(ExtractionOutput.model_validate(data), documents()).evidence
    assert evidence.kind.value == expected


def test_a_quote_matched_across_whitespace_is_stored_as_the_source_wrote_it() -> None:
    data = {"evidence": [{"id": "e1", "doc": "main", "paragraph": "p5", "kind": "code", "quote": "budget = RetryBudget(max_attempts=3)   runner.attach(budget)"}]}
    (evidence,) = accept(ExtractionOutput.model_validate(data), documents()).evidence
    assert evidence.quote == "budget = RetryBudget(max_attempts=3)\nrunner.attach(budget)"
