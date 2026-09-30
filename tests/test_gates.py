from __future__ import annotations

import factories as f
import pytest
from pydantic import ValidationError

from agent_daily_digest.contracts.articles import (
    GATE_ORDER,
    GATE_REASONS,
    GateExclusionReason,
    GateName,
    GateOutcome,
    GateResult,
)


def failed(gate: str, reason: str) -> dict[str, object]:
    return {"gate": gate, "passed": False, "reason": reason}


class TestGateResult:
    def test_passed_without_reason(self) -> None:
        result = GateResult.model_validate({"gate": "required_fields", "passed": True, "reason": None})
        assert result.passed

    def test_passed_with_reason_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GateResult.model_validate(
                {"gate": "required_fields", "passed": True, "reason": "missing_url"}
            )

    def test_failed_requires_reason(self) -> None:
        with pytest.raises(ValidationError):
            GateResult.model_validate({"gate": "required_fields", "passed": False, "reason": None})

    def test_reason_must_belong_to_gate(self) -> None:
        with pytest.raises(ValidationError):
            GateResult.model_validate(failed("required_fields", "already_processed_url"))

    def test_unknown_reason_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GateResult.model_validate(failed("required_fields", "looks_boring"))

    def test_passed_must_be_a_real_boolean(self) -> None:
        with pytest.raises(ValidationError):
            GateResult.model_validate({"gate": "required_fields", "passed": "yes", "reason": None})

    def test_unparseable_published_at_is_a_required_fields_reason(self) -> None:
        result = GateResult.model_validate(failed("required_fields", "invalid_published_at"))
        assert result.reason in GATE_REASONS[GateName.REQUIRED_FIELDS]

    def test_every_reason_belongs_to_exactly_one_gate(self) -> None:
        owners = [gate for reason in GateExclusionReason for gate in GATE_ORDER if reason in GATE_REASONS[gate]]
        assert len(owners) == len(GateExclusionReason)
        assert set(GATE_REASONS) == set(GateName)


class TestGateOutcome:
    def test_all_gates_passed(self) -> None:
        outcome = GateOutcome.model_validate(f.gate_outcome())
        assert outcome.passed
        assert outcome.exclusion_reasons == ()

    def test_short_circuit_on_first_failure(self) -> None:
        outcome = GateOutcome.model_validate(
            f.gate_outcome(results=[failed("required_fields", "missing_published_at")])
        )
        assert not outcome.passed
        assert outcome.exclusion_reasons == (GateExclusionReason.MISSING_PUBLISHED_AT,)

    def test_all_gates_evaluated_with_several_failures(self) -> None:
        results = f.passed_gate_results()
        results[1] = failed("content_available", "body_fetch_failed")
        results[2] = failed("not_previously_processed", "already_processed_content_hash")
        outcome = GateOutcome.model_validate(f.gate_outcome(results=results))
        assert not outcome.passed
        assert len(outcome.exclusion_reasons) == 2

    def test_pass_requires_every_gate(self) -> None:
        with pytest.raises(ValidationError):
            GateOutcome.model_validate(f.gate_outcome(results=f.passed_gate_results()[:3]))

    def test_gates_must_be_unique(self) -> None:
        results = f.passed_gate_results() + [f.passed_gate_results()[0]]
        with pytest.raises(ValidationError):
            GateOutcome.model_validate(f.gate_outcome(results=results))

    @pytest.mark.parametrize(
        "results",
        [
            [f.passed_gate_results()[0], failed("not_previously_processed", "already_processed_url")],
            [failed("content_available", "body_fetch_failed")],
            f.passed_gate_results()[:2],
            [failed("required_fields", "missing_url"), failed("content_available", "body_fetch_failed")],
        ],
        ids=["gap", "not-from-first-gate", "ends-with-pass", "continues-after-failure"],
    )
    def test_partial_evaluation_other_than_first_failure_is_forbidden(
        self, results: list[dict[str, object]]
    ) -> None:
        with pytest.raises(ValidationError):
            GateOutcome.model_validate(f.gate_outcome(results=results))

    def test_short_circuit_after_passed_gates(self) -> None:
        results = [*f.passed_gate_results()[:2], failed("not_previously_processed", "already_processed_url")]
        outcome = GateOutcome.model_validate(f.gate_outcome(results=results))
        assert outcome.exclusion_reasons == (GateExclusionReason.ALREADY_PROCESSED_URL,)

    def test_gates_must_follow_canonical_order(self) -> None:
        results = list(reversed(f.passed_gate_results()))
        with pytest.raises(ValidationError):
            GateOutcome.model_validate(f.gate_outcome(results=results))

    def test_results_are_required(self) -> None:
        with pytest.raises(ValidationError):
            GateOutcome.model_validate(f.gate_outcome(results=[]))
