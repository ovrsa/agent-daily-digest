from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

import factories as f
from digest_contracts import (
    MUST_READ_MAX,
    WORTH_KNOWING_MAX,
    AxisScores,
    CaveatStatement,
    Decision,
    DigestEntry,
    SelectorOutput,
    Tier,
)

FIXTURE = Path(__file__).parent / "fixtures" / "selector_output.valid.json"


def output() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def included(article_id: str) -> dict[str, Any]:
    item = copy.deepcopy(output()["must_read"][0])
    item["article_id"] = article_id
    return item


def excluded(article_id: str) -> dict[str, Any]:
    item = copy.deepcopy(output()["excluded"][0])
    item["article_id"] = article_id
    return item


def entry(**overrides: Any) -> dict[str, Any]:
    data = copy.deepcopy(output()["must_read"][0]["entry"])
    data.update(overrides)
    return data


class TestAxisScores:
    def test_boundaries_one_and_five(self) -> None:
        scores = AxisScores.model_validate(f.axis_scores(novelty=1, practicality=5))
        assert (scores.novelty, scores.practicality) == (1, 5)

    @pytest.mark.parametrize("value", [0, 6, -1, 2.5, 3.0, "3", True, None])
    def test_out_of_range_or_wrong_type(self, value: object) -> None:
        with pytest.raises(ValidationError):
            AxisScores.model_validate(f.axis_scores(reader_impact=value))

    @pytest.mark.parametrize("axis", list(f.axis_scores()))
    def test_every_axis_is_required(self, axis: str) -> None:
        data = f.axis_scores()
        del data[axis]
        with pytest.raises(ValidationError):
            AxisScores.model_validate(data)

    def test_no_total_score_field(self) -> None:
        with pytest.raises(ValidationError):
            AxisScores.model_validate(f.axis_scores(total=25))

    def test_json_float_is_not_an_integer_score(self) -> None:
        payload = json.dumps(f.axis_scores()).replace('"novelty": 3', '"novelty": 3.0')
        with pytest.raises(ValidationError):
            AxisScores.model_validate_json(payload)


class TestDigestEntry:
    def test_caveat_is_optional(self) -> None:
        parsed = DigestEntry.model_validate(entry(caveat=None))
        assert parsed.caveat is None
        data = entry()
        del data["caveat"]
        assert DigestEntry.model_validate(data).caveat is None

    def test_caveat_may_carry_no_evidence(self) -> None:
        """留保 can name something the article does not say, so it cites nothing."""
        text = "The post does not say which model version produced the numbers."
        parsed = DigestEntry.model_validate(entry(caveat={"text": text, "evidence_ids": []}))
        assert isinstance(parsed.caveat, CaveatStatement)
        assert parsed.caveat.evidence_ids == ()
        omitted = DigestEntry.model_validate(entry(caveat={"text": text}))
        assert omitted.caveat is not None and omitted.caveat.evidence_ids == ()

    def test_caveat_keeps_the_evidence_it_has(self) -> None:
        caveat = {"text": "The comparison covers one internal task set.", "evidence_ids": ["e1", "e2"]}
        parsed = DigestEntry.model_validate(entry(caveat=caveat))
        assert parsed.caveat is not None and parsed.caveat.evidence_ids == ("e1", "e2")

    @pytest.mark.parametrize(
        "evidence_ids",
        [["e1", "e1"], [""], ["   "], ["e" * 129]],
        ids=["duplicate", "empty", "blank", "too-long"],
    )
    def test_caveat_evidence_ids_keep_their_constraints(self, evidence_ids: list[str]) -> None:
        with pytest.raises(ValidationError):
            DigestEntry.model_validate(entry(caveat={"text": "caveat", "evidence_ids": evidence_ids}))

    @pytest.mark.parametrize("field", ["what_happened", "why_read", "evidence", "headline"])
    def test_core_fields_are_required(self, field: str) -> None:
        data = entry()
        del data[field]
        with pytest.raises(ValidationError):
            DigestEntry.model_validate(data)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"why_read": ""},
            {"why_read": "   "},
            {"headline": ""},
            {"headline": "   "},
            {"what_happened": {"text": " ", "evidence_ids": ["e1"]}},
            {"what_happened": {"text": "fact", "evidence_ids": []}},
            {"evidence": {"text": "fact", "evidence_ids": []}},
            {"evidence": {"text": "fact", "evidence_ids": ["e1", "e1"]}},
            {"evidence": {"text": "fact", "evidence_ids": [""]}},
            {"caveat": {"text": "", "evidence_ids": ["e1"]}},
            {"caveat": ""},
            {"what_happened": "plain text without evidence"},
        ],
    )
    def test_blank_text_and_missing_evidence_are_rejected(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            DigestEntry.model_validate(entry(**overrides))

    def test_why_read_is_editorial_text_without_evidence(self) -> None:
        with pytest.raises(ValidationError):
            DigestEntry.model_validate(entry(why_read={"text": "x", "evidence_ids": ["e1"]}))

    def test_the_headline_is_editorial_text_without_evidence(self) -> None:
        with pytest.raises(ValidationError):
            DigestEntry.model_validate(entry(headline={"text": "x", "evidence_ids": ["e1"]}))


class TestSelectorOutput:
    def test_valid_fixture_from_json(self) -> None:
        parsed = SelectorOutput.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
        assert parsed.decision_of("a001") == (Decision.INCLUDED, Tier.MUST_READ)
        assert parsed.decision_of("a002") == (Decision.INCLUDED, Tier.WORTH_KNOWING)
        assert parsed.decision_of("a003") == (Decision.EXCLUDED, None)
        assert parsed.article_ids == ("a001", "a002", "a003", "a004")

    def test_zero_adoption_is_valid(self) -> None:
        parsed = SelectorOutput.model_validate(
            {"must_read": [], "worth_knowing": [], "excluded": [excluded("a001")]}
        )
        assert parsed.included == ()

    def test_empty_input_is_valid(self) -> None:
        assert SelectorOutput.model_validate({"must_read": [], "worth_knowing": [], "excluded": []})

    @pytest.mark.parametrize("field", ["must_read", "worth_knowing", "excluded"])
    def test_lists_are_required(self, field: str) -> None:
        data = output()
        del data[field]
        with pytest.raises(ValidationError):
            SelectorOutput.model_validate(data)

    def test_must_read_at_limit(self) -> None:
        data = {"must_read": [included(f"m{i}") for i in range(MUST_READ_MAX)], "worth_knowing": [], "excluded": []}
        assert len(SelectorOutput.model_validate(data).must_read) == 5

    def test_must_read_over_limit(self) -> None:
        data = {"must_read": [included(f"m{i}") for i in range(MUST_READ_MAX + 1)], "worth_knowing": [], "excluded": []}
        with pytest.raises(ValidationError):
            SelectorOutput.model_validate(data)

    def test_worth_knowing_at_limit(self) -> None:
        data = {"must_read": [], "worth_knowing": [included(f"w{i}") for i in range(WORTH_KNOWING_MAX)], "excluded": []}
        assert len(SelectorOutput.model_validate(data).worth_knowing) == 8

    def test_worth_knowing_over_limit(self) -> None:
        data = {"must_read": [], "worth_knowing": [included(f"w{i}") for i in range(WORTH_KNOWING_MAX + 1)], "excluded": []}
        with pytest.raises(ValidationError):
            SelectorOutput.model_validate(data)

    def test_article_cannot_appear_twice(self) -> None:
        data = output()
        data["excluded"].append(excluded("a001"))
        with pytest.raises(ValidationError):
            SelectorOutput.model_validate(data)

    def test_included_article_requires_entry(self) -> None:
        data = output()
        del data["must_read"][0]["entry"]
        with pytest.raises(ValidationError):
            SelectorOutput.model_validate(data)

    def test_excluded_article_cannot_carry_entry(self) -> None:
        data = output()
        data["excluded"][0]["entry"] = entry()
        with pytest.raises(ValidationError):
            SelectorOutput.model_validate(data)

    @pytest.mark.parametrize("bucket", ["must_read", "excluded"])
    def test_scores_and_reason_are_required(self, bucket: str) -> None:
        for field in ("scores", "decision_reason"):
            data = output()
            del data[bucket][0][field]
            with pytest.raises(ValidationError):
                SelectorOutput.model_validate(data)

    def test_internal_reasoning_is_not_part_of_the_output(self) -> None:
        data = output()
        data["must_read"][0]["reasoning"] = "hidden chain of thought"
        with pytest.raises(ValidationError):
            SelectorOutput.model_validate(data)

    @pytest.mark.parametrize(
        "group",
        [
            {"representative_id": "a001", "duplicate_ids": ["a999"]},
            {"representative_id": "a999", "duplicate_ids": ["a004"]},
        ],
        ids=["unknown-duplicate", "unknown-representative"],
    )
    def test_unknown_article_in_duplicate_group(self, group: dict[str, Any]) -> None:
        data = output()
        data["duplicate_groups"] = [group]
        with pytest.raises(ValidationError):
            SelectorOutput.model_validate(data)

    def test_duplicate_of_representative_cannot_be_included(self) -> None:
        data = output()
        data["duplicate_groups"] = [{"representative_id": "a001", "duplicate_ids": ["a002"]}]
        with pytest.raises(ValidationError):
            SelectorOutput.model_validate(data)

    @pytest.mark.parametrize(
        "groups",
        [
            [{"representative_id": "a001", "duplicate_ids": []}],
            [{"representative_id": "a003", "duplicate_ids": ["a003"]}],
            [
                {"representative_id": "a001", "duplicate_ids": ["a004"]},
                {"representative_id": "a002", "duplicate_ids": ["a004"]},
            ],
            [
                {"representative_id": "a001", "duplicate_ids": ["a004"]},
                {"representative_id": "a004", "duplicate_ids": ["a003"]},
            ],
        ],
        ids=["empty", "self", "shared-duplicate", "chained"],
    )
    def test_duplicate_groups_are_disjoint_and_non_trivial(self, groups: list[dict[str, Any]]) -> None:
        data = output()
        data["duplicate_groups"] = groups
        with pytest.raises(ValidationError):
            SelectorOutput.model_validate(data)

    def test_representative_may_be_excluded(self) -> None:
        data = output()
        data["duplicate_groups"] = [{"representative_id": "a003", "duplicate_ids": ["a004"]}]
        assert SelectorOutput.model_validate(data).duplicate_groups[0].representative_id == "a003"

    def test_unknown_article_lookup(self) -> None:
        with pytest.raises(KeyError):
            SelectorOutput.model_validate(output()).decision_of("a999")


class TestSelectorStructuredOutput:
    schema = SelectorOutput.model_json_schema()

    def test_valid_fixture_satisfies_schema(self) -> None:
        Draft202012Validator(self.schema).validate(output())

    def test_limits_are_in_the_schema(self) -> None:
        properties = self.schema["properties"]
        assert properties["must_read"]["maxItems"] == MUST_READ_MAX
        assert properties["worth_knowing"]["maxItems"] == WORTH_KNOWING_MAX
        assert "minItems" not in properties["must_read"]
        assert "minItems" not in properties["worth_knowing"]

    def test_schema_rejects_over_limit_and_out_of_range(self) -> None:
        data = output()
        data["must_read"] = [included(f"m{i}") for i in range(MUST_READ_MAX + 1)]
        data["excluded"][0]["scores"]["novelty"] = 6
        errors = list(Draft202012Validator(self.schema).iter_errors(data))
        assert {e.validator for e in errors} >= {"maxItems", "maximum"}

    def test_only_the_caveat_may_omit_evidence(self) -> None:
        defs = self.schema["$defs"]
        fact, caveat = defs["FactStatement"], defs["CaveatStatement"]
        assert list(caveat["properties"]) == ["text", "evidence_ids"]
        assert fact["properties"]["evidence_ids"]["minItems"] == 1
        assert "minItems" not in caveat["properties"]["evidence_ids"]
        assert set(fact["required"]) == {"text", "evidence_ids"}
        assert set(caveat["required"]) == {"text"}

    def test_schema_accepts_a_caveat_without_evidence(self) -> None:
        data = output()
        data["must_read"][0]["entry"]["caveat"] = {"text": "The model version is not given.", "evidence_ids": []}
        Draft202012Validator(self.schema).validate(data)
        assert SelectorOutput.model_validate(data).must_read[0].entry.caveat is not None

    def test_scores_come_before_reason_and_entry(self) -> None:
        included_def = self.schema["$defs"]["IncludedArticle"]
        assert list(included_def["properties"]) == ["article_id", "scores", "decision_reason", "entry"]
