"""JSON Schema output for every public model, and its use as a structured-output schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel

import digest_contracts
from digest_contracts import SOURCE_EVIDENCE_MAX_CHARS, JudgeReport

FIXTURES = Path(__file__).parent / "fixtures"

PUBLIC_MODELS = sorted(
    (
        obj
        for name in digest_contracts.__all__
        if isinstance(obj := getattr(digest_contracts, name), type)
        and issubclass(obj, BaseModel)
        and obj is not digest_contracts.ContractModel
    ),
    key=lambda model: model.__name__,
)


def object_schemas(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return [schema, *schema.get("$defs", {}).values()]


def test_every_public_model_is_covered() -> None:
    assert len(PUBLIC_MODELS) >= 20


@pytest.mark.parametrize("model", PUBLIC_MODELS, ids=lambda m: m.__name__)
@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_schema_is_json_and_closed(model: type[BaseModel], mode: str) -> None:
    schema = model.model_json_schema(mode=mode)  # type: ignore[arg-type]
    Draft202012Validator.check_schema(schema)
    json.dumps(schema)
    for definition in object_schemas(schema):
        if definition.get("type") == "object":
            assert definition.get("additionalProperties") is False, definition.get("title")


class TestJudgeStructuredOutput:
    schema = JudgeReport.model_json_schema()

    def fixture(self) -> dict[str, Any]:
        return json.loads((FIXTURES / "judge_report.valid.json").read_text(encoding="utf-8"))

    def test_valid_fixture_satisfies_schema_and_model(self) -> None:
        data = self.fixture()
        Draft202012Validator(self.schema).validate(data)
        JudgeReport.model_validate(data)

    def test_schema_rejects_what_the_model_rejects(self) -> None:
        data = self.fixture()
        data["findings"][0]["assessment"]["severity"] = "critical"
        data["findings"][1]["assessment"]["source_evidence"] = "x" * (SOURCE_EVIDENCE_MAX_CHARS + 1)
        del data["findings"][1]["improvement"]
        errors = list(Draft202012Validator(self.schema).iter_errors(data))
        assert len(errors) == 3

    def test_assessment_is_generated_before_improvement(self) -> None:
        finding = self.schema["$defs"]["JudgeFinding"]
        assert list(finding["properties"]) == ["finding_id", "article_id", "assessment", "improvement"]
        assert set(finding["required"]) == set(finding["properties"])

    def test_scales_are_closed_enums(self) -> None:
        defs = self.schema["$defs"]
        assert defs["Severity"]["enum"] == ["high", "medium", "low"]
        assert defs["Confidence"]["enum"] == ["high", "medium", "low"]
        assert len(defs["AuditCategory"]["enum"]) == 10
