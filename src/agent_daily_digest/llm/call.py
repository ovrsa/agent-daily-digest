"""The measurement boundary every LLM call goes through.

Research, Selector and Judge each make their call through `measured_call`. It
times each attempt, prices it, classifies its failure, retries within a fixed
policy and hands the finished `LLMCallMetrics` to the recorder, including when
the call is interrupted. The provider is behind `Invoke`, so this module never
imports the SDK: `agent_daily_digest.llm.client` supplies the real invoker and tests supply fakes.

How a response is judged comes from the #2 spike, not from the SDK's own flags:

- `is_error` decides failure, never `subtype`: an unknown model came back as
  `subtype="success"` with `is_error=True`
- `is_error=False` does not mean there is an answer: a call that could not
  satisfy the schema came back with `structured_output=None`, which is a
  parse failure here
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import ValidationError

from agent_daily_digest.contracts.base import ErrorKind, ErrorRecord, ValidationIssue
from agent_daily_digest.contracts.metrics import (
    AttemptStatus,
    LLMAttempt,
    LLMCallMetrics,
    LLMRole,
)
from agent_daily_digest.llm.pricing import (
    ModelUsage,
    PricingTable,
    estimate_cost,
    token_usage,
)
from agent_daily_digest.observe.store import (
    Clock,
    describe_exception,
    safe_detail,
    utc_now,
)

MIN_MAX_TURNS = 6


DEFAULT_MAX_TURNS = 8


DEFAULT_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class StructuredRequest:
    """One request. `schema` is the JSON Schema the output must satisfy."""

    model: str
    system_prompt: str
    prompt: str
    schema: Mapping[str, Any]
    max_turns: int = DEFAULT_MAX_TURNS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_budget_usd: float | None = None
    """Passed to the CLI, which stops the call once its list-price cost passes this."""

    def __post_init__(self) -> None:
        if self.max_turns < MIN_MAX_TURNS:
            raise ValueError(f"max_turns below {MIN_MAX_TURNS} leaves no room for schema retries")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


T = TypeVar("T")

DEFAULT_MAX_ATTEMPTS = 3
"""One call and two retries. Enough for a transient failure, and a validation
failure that repeats three times is a prompt or contract problem, not luck."""


@dataclass(frozen=True)
class LLMResponse:
    """What one attempt returned, in the shape the boundary needs."""

    structured_output: Any
    is_error: bool = False
    api_error_status: int | None = None
    subtype: str | None = None
    usage: tuple[ModelUsage, ...] = ()


class LLMInvocationError(Exception):
    """The invoker could not get a response. `kind` is already classified."""

    def __init__(
        self,
        kind: ErrorKind,
        detail: str | None = None,
        *,
        retryable: bool = False,
        usage: tuple[ModelUsage, ...] = (),
    ) -> None:
        super().__init__(detail or kind.value)
        self.kind = kind
        self.detail = detail
        self.retryable = retryable
        self.usage = usage


class OutputRejected(Exception):
    """The output parsed but broke a rule the schema cannot express.

    `issues` name where and what, never the rejected value, so they can go to
    metrics the same way Pydantic's errors do.
    """

    def __init__(self, issues: tuple[ValidationIssue, ...]) -> None:
        if not issues:
            raise ValueError("OutputRejected needs at least one issue")
        super().__init__(f"{len(issues)} issue(s)")
        self.issues = issues


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff_seconds: float = 2.0
    """Wait before retrying a transient failure, multiplied by the attempt number.
    A validation or parse failure is retried at once: waiting does not change it."""

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")


@dataclass(frozen=True)
class CallSpec:
    call_id: str
    role: LLMRole
    model: str
    prompt_version: str


@dataclass(frozen=True)
class CallOutcome(Generic[T]):
    """The parsed value, or the last failure. `metrics` is always complete."""

    value: T | None
    metrics: LLMCallMetrics
    error: ErrorRecord | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


Invoke = Callable[[int], LLMResponse]
"""Makes attempt number N and returns its response, or raises `LLMInvocationError`."""

Parse = Callable[[Any], T]
"""Turns `structured_output` into the typed value. Raises `ValidationError` or `OutputRejected`."""


@dataclass
class _Failure:
    error: ErrorRecord
    retryable: bool
    transient: bool = False
    issues: tuple[ValidationIssue, ...] = field(default_factory=tuple)


class _AttemptFailed(Exception):
    """A response that is itself a failure, raised so one handler records every failed attempt."""

    def __init__(self, failure: _Failure, usage: tuple[ModelUsage, ...] = ()) -> None:
        super().__init__(failure.error.kind.value)
        self.failure = failure
        self.usage = usage


def measured_call(
    spec: CallSpec,
    invoke: Invoke,
    parse: Parse[T],
    *,
    pricing: PricingTable,
    policy: RetryPolicy = RetryPolicy(),
    clock: Clock = utc_now,
    sleep: Callable[[float], None] = time.sleep,
    record: Callable[[LLMCallMetrics], None] | None = None,
) -> CallOutcome[T]:
    """Run one logical call with retries, and record it exactly once.

    A failure the policy does not retry ends the call with a failed outcome,
    which is how a caller tells a Judge failure from a Judge report. An
    interruption (any `BaseException` that is not an `Exception`, such as
    `KeyboardInterrupt` or task cancellation) is recorded as a failed attempt
    of kind `cancelled` and then re-raised.
    """
    attempts: list[LLMAttempt] = []
    outcome: CallOutcome[T] | None = None
    try:
        for number in range(1, policy.max_attempts + 1):
            started = clock()
            usage: tuple[ModelUsage, ...] = ()
            try:
                response = invoke(number)
                usage = response.usage
                value = parse(_structured(response))
            except _AttemptFailed as failed:
                failure = failed.failure
                usage = failed.usage or usage
            except LLMInvocationError as exc:
                failure = _Failure(
                    error=ErrorRecord(kind=exc.kind, detail=safe_detail(exc.detail)),
                    retryable=exc.retryable,
                    transient=exc.retryable,
                )
                usage = exc.usage
            except ValidationError as exc:
                issues = ValidationIssue.from_validation_error(exc)
                failure = _validation_failure(issues)
            except OutputRejected as exc:
                failure = _validation_failure(exc.issues)
            except (TimeoutError, asyncio.TimeoutError):
                failure = _Failure(
                    error=ErrorRecord(kind=ErrorKind.TIMEOUT, detail="attempt timed out"),
                    retryable=True,
                    transient=True,
                )
            except Exception as exc:
                # A bug in the invoker or the parser. It is recorded, then raised,
                # because swallowing it would turn a defect into a quiet failure.
                attempts.append(
                    _attempt(
                        number,
                        started,
                        clock(),
                        usage,
                        pricing,
                        ErrorRecord(kind=ErrorKind.UNEXPECTED, detail=describe_exception(exc)),
                    )
                )
                raise
            except BaseException as exc:
                # KeyboardInterrupt, task cancellation, SystemExit, GeneratorExit: the
                # call was interrupted, not wrong. Recorded, then left to propagate.
                attempts.append(
                    _attempt(
                        number,
                        started,
                        clock(),
                        usage,
                        pricing,
                        ErrorRecord(kind=ErrorKind.CANCELLED, detail=describe_exception(exc)),
                    )
                )
                raise
            else:
                attempts.append(_attempt(number, started, clock(), usage, pricing, None))
                outcome = CallOutcome(value=value, metrics=_metrics(spec, attempts))
                return outcome

            attempts.append(_attempt(number, started, clock(), usage, pricing, failure.error, failure.issues))
            if not failure.retryable or number == policy.max_attempts:
                outcome = CallOutcome(value=None, metrics=_metrics(spec, attempts), error=failure.error)
                return outcome
            if failure.transient and policy.backoff_seconds > 0:
                sleep(policy.backoff_seconds * number)
        raise AssertionError("unreachable: the loop returns on its last attempt")
    finally:
        if record is not None and attempts:
            record(outcome.metrics if outcome is not None else _metrics(spec, attempts))


def _structured(response: LLMResponse) -> Any:
    if response.is_error:
        raise _AttemptFailed(_response_failure(response), response.usage)
    if response.structured_output is None:
        raise _AttemptFailed(
            _Failure(
                error=ErrorRecord(kind=ErrorKind.PARSE, detail="no structured output"),
                retryable=True,
            ),
            response.usage,
        )
    return response.structured_output


def _response_failure(response: LLMResponse) -> _Failure:
    """Classify an `is_error` response by status, as the #2 spike measured it."""
    status = response.api_error_status
    subtype = response.subtype or ""
    label = f"subtype={subtype or '-'} status={status if status is not None else '-'}"
    if subtype == "error_max_turns":
        # The model spent its turns without producing a valid structured output.
        return _Failure(ErrorRecord(kind=ErrorKind.PARSE, detail=label), retryable=True)
    if status in (401, 403):
        return _Failure(ErrorRecord(kind=ErrorKind.AUTHENTICATION, detail=label), retryable=False)
    if status == 429:
        return _Failure(ErrorRecord(kind=ErrorKind.RATE_LIMIT, detail=label), retryable=True, transient=True)
    if status is not None and status >= 500:
        return _Failure(ErrorRecord(kind=ErrorKind.HTTP_STATUS, detail=label), retryable=True, transient=True)
    if status is not None:
        return _Failure(ErrorRecord(kind=ErrorKind.HTTP_STATUS, detail=label), retryable=False)
    return _Failure(ErrorRecord(kind=ErrorKind.UNEXPECTED, detail=label), retryable=False)


def _validation_failure(issues: tuple[ValidationIssue, ...]) -> _Failure:
    return _Failure(
        error=ErrorRecord(kind=ErrorKind.VALIDATION, detail=f"{len(issues)} validation issue(s)"),
        retryable=True,
        issues=issues,
    )


def _attempt(
    number: int,
    started: datetime,
    ended: datetime,
    usage: tuple[ModelUsage, ...],
    pricing: PricingTable,
    error: ErrorRecord | None,
    issues: tuple[ValidationIssue, ...] = (),
) -> LLMAttempt:
    return LLMAttempt(
        attempt_number=number,
        status=AttemptStatus.FAILED if error is not None else AttemptStatus.SUCCEEDED,
        started_at=started,
        ended_at=max(ended, started),
        usage=token_usage(usage),
        cost=estimate_cost(usage, pricing),
        error=error,
        validation_issues=issues,
    )


def _metrics(spec: CallSpec, attempts: list[LLMAttempt]) -> LLMCallMetrics:
    return LLMCallMetrics(
        call_id=spec.call_id,
        role=spec.role,
        model=spec.model,
        prompt_version=spec.prompt_version,
        attempts=tuple(attempts),
    )


def invocation_error(exc: BaseException, kind: ErrorKind, *, retryable: bool) -> LLMInvocationError:
    """Wrap a provider exception without carrying its message into metrics."""
    return LLMInvocationError(kind, describe_exception(exc), retryable=retryable)
