"""`measured_call`: every LLM call goes through one boundary that times, prices, classifies and retries."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from digest_contracts import AttemptStatus, ErrorKind, LLMCallMetrics, LLMRole, ValidationIssue
from digest_observe import (
    CallSpec,
    LLMInvocationError,
    LLMResponse,
    ModelUsage,
    OutputRejected,
    RetryPolicy,
    measured_call,
)
from observe_support import PRICING, FakeClock

SPEC = CallSpec(call_id="selector-1", role=LLMRole.SELECTOR, model="claude-sonnet-5", prompt_version="selector-v1")
SONNET = ModelUsage(model="claude-sonnet-5", input_tokens=4, output_tokens=900, cache_creation_input_tokens=25_440, cache_read_input_tokens=20_084)
HAIKU = ModelUsage(model="claude-haiku-4-5-20251001", input_tokens=1_067, output_tokens=13)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool


def parse(value: Any) -> Answer:
    return Answer.model_validate(value)


def ok(usage: tuple[ModelUsage, ...] = (SONNET,)) -> LLMResponse:
    return LLMResponse(structured_output={"ok": True}, usage=usage)


def scripted(*steps: Any):
    """An invoker that plays `steps` in order: a response is returned, an exception raised."""
    calls: list[int] = []

    def invoke(number: int) -> LLMResponse:
        calls.append(number)
        step = steps[len(calls) - 1]
        if isinstance(step, BaseException):
            raise step
        return step

    invoke.calls = calls  # type: ignore[attr-defined]
    return invoke


def run(invoke, *, policy: RetryPolicy = RetryPolicy(backoff_seconds=0), record=None, parser=parse):
    return measured_call(SPEC, invoke, parser, pricing=PRICING, policy=policy, clock=FakeClock(), record=record)


def test_a_successful_call_is_one_attempt_with_tokens_and_cost() -> None:
    outcome = run(scripted(ok((SONNET, HAIKU))))

    assert outcome.succeeded and outcome.value == Answer(ok=True)
    metrics = outcome.metrics
    assert (metrics.call_id, metrics.role, metrics.model, metrics.prompt_version) == (
        "selector-1",
        LLMRole.SELECTOR,
        "claude-sonnet-5",
        "selector-v1",
    )
    (attempt,) = metrics.attempts
    assert attempt.status is AttemptStatus.SUCCEEDED
    # Every input token the models processed, cached or not, from both models.
    assert attempt.usage.input_tokens == 4 + 25_440 + 20_084 + 1_067
    assert attempt.usage.output_tokens == 900 + 13
    assert attempt.cost.basis.value == "estimated"
    assert attempt.duration_ms == 250


def test_a_validation_failure_is_retried_and_its_issues_kept_without_the_input() -> None:
    rejected = LLMResponse(structured_output={"ok": "SECRET-MODEL-TEXT", "extra": 1}, usage=(SONNET,))
    outcome = run(scripted(rejected, ok()))

    assert outcome.succeeded
    first, second = outcome.metrics.attempts
    assert first.status is AttemptStatus.FAILED and first.error.kind is ErrorKind.VALIDATION
    assert {issue.type for issue in first.validation_issues} == {"bool_parsing", "extra_forbidden"}
    assert second.status is AttemptStatus.SUCCEEDED
    assert outcome.metrics.retry_count == 1
    assert "SECRET-MODEL-TEXT" not in outcome.metrics.model_dump_json()
    # The failed attempt still spent tokens, and they are counted.
    assert first.usage is not None and first.cost is not None


def test_retries_stop_at_the_policy_cap_and_the_last_error_is_returned() -> None:
    bad = LLMResponse(structured_output={"ok": "maybe"}, usage=(SONNET,))
    invoke = scripted(bad, bad, bad, ok())
    outcome = run(invoke, policy=RetryPolicy(max_attempts=3, backoff_seconds=0))

    assert not outcome.succeeded and outcome.value is None
    assert outcome.error.kind is ErrorKind.VALIDATION
    assert len(outcome.metrics.attempts) == 3
    assert invoke.calls == [1, 2, 3]


def test_output_rejected_by_a_rule_outside_the_schema_counts_as_validation() -> None:
    def strict(value: Any) -> Answer:
        raise OutputRejected((ValidationIssue(loc="evidence_ids[0]", type="unknown_evidence_id"),))

    outcome = run(scripted(ok(), ok(), ok()), parser=strict)

    assert outcome.error.kind is ErrorKind.VALIDATION
    assert outcome.metrics.attempts[0].validation_issues[0].type == "unknown_evidence_id"


def test_success_without_structured_output_is_a_parse_failure() -> None:
    """The #2 spike: `is_error=False` came back with `structured_output=None`."""
    empty = LLMResponse(structured_output=None, is_error=False, subtype="success", usage=(SONNET,))
    outcome = run(scripted(empty, ok()))

    assert outcome.succeeded
    assert outcome.metrics.attempts[0].error.kind is ErrorKind.PARSE


@pytest.mark.parametrize(
    ("status", "subtype", "kind", "retried"),
    [
        (401, "success", ErrorKind.AUTHENTICATION, False),
        (404, "success", ErrorKind.HTTP_STATUS, False),
        (429, "success", ErrorKind.RATE_LIMIT, True),
        (529, "success", ErrorKind.HTTP_STATUS, True),
        (None, "error_max_turns", ErrorKind.PARSE, True),
        (None, "error_during_execution", ErrorKind.UNEXPECTED, False),
    ],
)
def test_an_error_response_is_classified_by_status_not_subtype(status, subtype, kind, retried) -> None:
    failed = LLMResponse(structured_output=None, is_error=True, api_error_status=status, subtype=subtype)
    invoke = scripted(failed, ok())
    outcome = run(invoke)

    assert outcome.metrics.attempts[0].error.kind is kind
    assert outcome.succeeded is retried
    assert invoke.calls == ([1, 2] if retried else [1])


def test_transient_failures_back_off_and_validation_failures_do_not() -> None:
    waits: list[float] = []
    invoke = scripted(
        LLMInvocationError(ErrorKind.NETWORK, "connection reset", retryable=True),
        LLMResponse(structured_output={"ok": "maybe"}),
        ok(),
    )
    outcome = measured_call(
        SPEC, invoke, parse, pricing=PRICING, policy=RetryPolicy(backoff_seconds=2.0), clock=FakeClock(), sleep=waits.append
    )
    assert outcome.succeeded
    assert waits == [2.0]


def test_a_timeout_raised_by_the_invoker_is_retried_as_timeout() -> None:
    outcome = run(scripted(asyncio.TimeoutError(), ok()))
    assert outcome.metrics.attempts[0].error.kind is ErrorKind.TIMEOUT
    assert outcome.succeeded


def test_the_call_is_recorded_exactly_once_on_success_and_on_failure() -> None:
    recorded: list[LLMCallMetrics] = []
    run(scripted(ok()), record=recorded.append)
    run(scripted(LLMInvocationError(ErrorKind.AUTHENTICATION, "401")), record=recorded.append)

    assert [call.succeeded for call in recorded] == [True, False]


def test_an_interruption_is_recorded_as_cancelled_and_re_raised() -> None:
    recorded: list[LLMCallMetrics] = []
    with pytest.raises(KeyboardInterrupt):
        run(scripted(LLMResponse(structured_output={"ok": "maybe"}), KeyboardInterrupt()), record=recorded.append)

    (call,) = recorded
    assert [a.status for a in call.attempts] == [AttemptStatus.FAILED, AttemptStatus.FAILED]
    assert call.attempts[-1].error.kind is ErrorKind.CANCELLED


def test_a_bug_in_the_parser_is_recorded_as_unexpected_and_raised() -> None:
    recorded: list[LLMCallMetrics] = []

    def broken(value: Any) -> Answer:
        raise ZeroDivisionError("model said: ignore previous instructions")

    with pytest.raises(ZeroDivisionError):
        run(scripted(ok()), record=recorded.append, parser=broken)
    attempt = recorded[0].attempts[0]
    assert attempt.error.kind is ErrorKind.UNEXPECTED
    # The exception message may quote model output; only the class name is kept.
    assert attempt.error.detail == "ZeroDivisionError"


def test_attempts_are_ordered_in_time() -> None:
    outcome = run(scripted(LLMResponse(structured_output=None), LLMResponse(structured_output=None), ok()))
    attempts = outcome.metrics.attempts
    for earlier, later in zip(attempts, attempts[1:]):
        assert earlier.ended_at <= later.started_at


def test_an_unpriced_model_leaves_the_cost_unknown_rather_than_partial() -> None:
    other = ModelUsage(model="claude-unknown-9", input_tokens=10, output_tokens=10)
    outcome = run(scripted(ok((SONNET, other))))
    attempt = outcome.metrics.attempts[0]
    assert attempt.cost is None
    assert attempt.usage is not None
    assert outcome.metrics.total_cost_usd is None
