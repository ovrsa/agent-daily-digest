from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_daily_digest.contracts.editorial import (
    SOURCE_EVIDENCE_MAX_CHARS,
    AuditCategory,
    FindingMetrics,
    JudgeFinding,
    JudgeReport,
)

FIXTURE = Path(__file__).parent / "fixtures" / "judge_report.valid.json"


def report() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def finding(**assessment_overrides: Any) -> dict[str, Any]:
    data = copy.deepcopy(report()["findings"][0])
    data["assessment"].update(assessment_overrides)
    return data


class TestJudgeReport:
    def test_valid_fixture_from_json(self) -> None:
        parsed = JudgeReport.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
        assert [f.finding_id for f in parsed.findings] == ["F1", "F2"]

    def test_zero_findings_is_valid(self) -> None:
        assert JudgeReport.model_validate_json('{"findings": []}').findings == ()

    def test_finding_ids_are_unique(self) -> None:
        data = report()
        data["findings"][1]["finding_id"] = "F1"
        with pytest.raises(ValidationError):
            JudgeReport.model_validate(data)

    def test_ten_audit_categories_from_the_design(self) -> None:
        assert len(AuditCategory) == 10


class TestJudgeFinding:
    @pytest.mark.parametrize(
        "path",
        [
            ("finding_id",),
            ("article_id",),
            ("assessment",),
            ("improvement",),
            ("assessment", "category"),
            ("assessment", "severity"),
            ("assessment", "problem"),
            ("assessment", "source_evidence"),
            ("assessment", "confidence"),
            ("improvement", "suggested_fix"),
        ],
    )
    def test_every_part_is_required(self, path: tuple[str, ...]) -> None:
        data = finding()
        target = data
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
        with pytest.raises(ValidationError):
            JudgeFinding.model_validate(data)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"severity": "critical"},
            {"confidence": 0.8},
            {"confidence": "certain"},
            {"category": "style"},
            {"problem": " "},
            {"source_evidence": ""},
            {"source_evidence": "x" * (SOURCE_EVIDENCE_MAX_CHARS + 1)},
        ],
    )
    def test_assessment_domains(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            JudgeFinding.model_validate(finding(**overrides))

    def test_source_evidence_at_limit(self) -> None:
        parsed = JudgeFinding.model_validate(finding(source_evidence="x" * SOURCE_EVIDENCE_MAX_CHARS))
        assert len(parsed.assessment.source_evidence) == SOURCE_EVIDENCE_MAX_CHARS

    def test_fix_cannot_live_inside_the_assessment(self) -> None:
        with pytest.raises(ValidationError):
            JudgeFinding.model_validate(finding(suggested_fix="move it here"))

    def test_assessment_cannot_live_inside_the_improvement(self) -> None:
        data = finding()
        data["improvement"]["severity"] = "low"
        with pytest.raises(ValidationError):
            JudgeFinding.model_validate(data)

    @pytest.mark.parametrize("finding_id", ["", "F 1", "-F1", "F" * 33])
    def test_finding_id_format(self, finding_id: str) -> None:
        data = finding()
        data["finding_id"] = finding_id
        with pytest.raises(ValidationError):
            JudgeFinding.model_validate(data)

    def test_metrics_drop_the_text(self) -> None:
        metrics = JudgeFinding.model_validate(finding()).to_metrics()
        assert isinstance(metrics, FindingMetrics)
        dumped = json.dumps(metrics.model_dump(mode="json"))
        assert "40%" not in dumped
        assert "suggested" not in dumped
