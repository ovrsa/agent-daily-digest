"""Run, stage and LLM call metrics, including the allowed status transitions."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Annotated, Any

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from agent_daily_digest.contracts.base import ArticleId, ContractModel, HttpUrlStr, InvalidTransitionError, NonBlankStr, NonNegativeInt, SourceId, first_duplicate
from agent_daily_digest.contracts.articles import BodySource, SourceMetrics
from agent_daily_digest.contracts.base import ErrorKind, ErrorRecord, ValidationIssue
from agent_daily_digest.contracts.articles import GateOutcome
from agent_daily_digest.contracts.editorial import FindingMetrics
from agent_daily_digest.contracts.editorial import MUST_READ_MAX, WORTH_KNOWING_MAX, AxisScores, Decision, Tier

RecordId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
"""Identifier of a run or an LLM call."""
ShortLabel = Annotated[NonBlankStr, StringConstraints(max_length=100)]


def _duration_ms(started_at: datetime | None, ended_at: datetime | None) -> int | None:
    if started_at is None or ended_at is None:
        return None
    return round((ended_at - started_at).total_seconds() * 1000)


def _check_order(started_at: datetime | None, ended_at: datetime | None) -> None:
    if started_at is not None and ended_at is not None and ended_at < started_at:
        raise ValueError("ended_at must not be earlier than started_at")


class StageName(str, Enum):
    COLLECT = "collect"
    NORMALIZE = "normalize"
    GATE = "gate"
    RESEARCH = "research"
    SELECT = "select"
    RENDER = "render"
    PUBLISH = "publish"
    JUDGE = "judge"
    COMMENT = "comment"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


ALLOWED_STAGE_TRANSITIONS: Mapping[StageStatus, frozenset[StageStatus]] = MappingProxyType(
    {
        StageStatus.PENDING: frozenset({StageStatus.RUNNING, StageStatus.SKIPPED}),
        StageStatus.RUNNING: frozenset({StageStatus.SUCCEEDED, StageStatus.FAILED}),
        StageStatus.SUCCEEDED: frozenset(),
        StageStatus.FAILED: frozenset(),
        StageStatus.SKIPPED: frozenset(),
    }
)
TERMINAL_STAGE_STATUSES = frozenset(s for s, nxt in ALLOWED_STAGE_TRANSITIONS.items() if not nxt)


class StageMetrics(ContractModel):
    stage: StageName
    status: StageStatus
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    error: ErrorRecord | None = None

    @property
    def duration_ms(self) -> int | None:
        return _duration_ms(self.started_at, self.ended_at)

    @model_validator(mode="after")
    def _check_status_fields(self) -> StageMetrics:
        started = self.status not in (StageStatus.PENDING, StageStatus.SKIPPED)
        ended = self.status in (StageStatus.SUCCEEDED, StageStatus.FAILED)
        if (self.started_at is not None) != started:
            raise ValueError(f"started_at does not match status {self.status.value}")
        if (self.ended_at is not None) != ended:
            raise ValueError(f"ended_at does not match status {self.status.value}")
        if (self.error is not None) != (self.status is StageStatus.FAILED):
            raise ValueError("error is required for failed stages and forbidden otherwise")
        _check_order(self.started_at, self.ended_at)
        return self

    def _transition(self, to: StageStatus, **changes: Any) -> StageMetrics:
        if to not in ALLOWED_STAGE_TRANSITIONS[self.status]:
            raise InvalidTransitionError(f"stage {self.status.value} -> {to.value} is not allowed")
        return type(self).model_validate({**self.model_dump(), "status": to, **changes})

    def start(self, at: datetime) -> StageMetrics:
        return self._transition(StageStatus.RUNNING, started_at=at)

    def succeed(self, at: datetime) -> StageMetrics:
        return self._transition(StageStatus.SUCCEEDED, ended_at=at)

    def fail(self, at: datetime, error: ErrorRecord) -> StageMetrics:
        return self._transition(StageStatus.FAILED, ended_at=at, error=error)

    def skip(self) -> StageMetrics:
        return self._transition(StageStatus.SKIPPED)


class LLMRole(str, Enum):
    RESEARCH = "research"
    SELECTOR = "selector"
    JUDGE = "judge"


class TokenUsage(ContractModel):
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt


class CostBasis(str, Enum):
    ESTIMATED = "estimated"
    MEASURED = "measured"


class CostRecord(ContractModel):
    usd: float = Field(ge=0, allow_inf_nan=False)
    basis: CostBasis


class AttemptStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class LLMAttempt(ContractModel):
    """One request to the model. `usage` and `cost` are absent when the provider reports none."""

    attempt_number: int = Field(ge=1, strict=True)
    status: AttemptStatus
    started_at: AwareDatetime
    ended_at: AwareDatetime
    usage: TokenUsage | None = None
    cost: CostRecord | None = None
    error: ErrorRecord | None = None
    validation_issues: tuple[ValidationIssue, ...] = ()

    @property
    def duration_ms(self) -> int:
        return round((self.ended_at - self.started_at).total_seconds() * 1000)

    @model_validator(mode="after")
    def _check_status_fields(self) -> LLMAttempt:
        _check_order(self.started_at, self.ended_at)
        if (self.error is not None) != (self.status is AttemptStatus.FAILED):
            raise ValueError("error is required for failed attempts and forbidden otherwise")
        if self.validation_issues and (self.error is None or self.error.kind is not ErrorKind.VALIDATION):
            raise ValueError("validation_issues require an error of kind validation")
        return self


class LLMCallMetrics(ContractModel):
    """One logical LLM call (Selector, Judge or Research) and its retries."""

    call_id: RecordId
    role: LLMRole
    model: ShortLabel
    prompt_version: ShortLabel
    attempts: tuple[LLMAttempt, ...] = Field(min_length=1)

    @property
    def succeeded(self) -> bool:
        return self.attempts[-1].status is AttemptStatus.SUCCEEDED

    @property
    def retry_count(self) -> int:
        return len(self.attempts) - 1

    @property
    def total_input_tokens(self) -> int | None:
        usages = [a.usage for a in self.attempts]
        return None if None in usages else sum(u.input_tokens for u in usages)  # type: ignore[union-attr]

    @property
    def total_output_tokens(self) -> int | None:
        usages = [a.usage for a in self.attempts]
        return None if None in usages else sum(u.output_tokens for u in usages)  # type: ignore[union-attr]

    @property
    def total_cost_usd(self) -> float | None:
        costs = [a.cost for a in self.attempts]
        return None if None in costs else sum(c.usd for c in costs)  # type: ignore[union-attr]

    @model_validator(mode="after")
    def _check_attempts(self) -> LLMCallMetrics:
        numbers = [attempt.attempt_number for attempt in self.attempts]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("attempt_number must count up from 1 without gaps")
        if any(a.status is not AttemptStatus.FAILED for a in self.attempts[:-1]):
            raise ValueError("a retry is allowed only after a failed attempt")
        return self


class ArticleMetrics(ContractModel):
    """Per-article record: gate result, scores and decision, without the body."""

    article_id: ArticleId
    source_id: SourceId
    canonical_url: HttpUrlStr | None = None
    body_source: BodySource | None = None
    char_count: NonNegativeInt | None = None
    gate: GateOutcome
    scores: AxisScores | None = None
    decision: Decision | None = None
    tier: Tier | None = None
    decision_reason: NonBlankStr | None = None

    @model_validator(mode="after")
    def _check_selection(self) -> ArticleMetrics:
        if self.gate.article_id != self.article_id:
            raise ValueError("gate.article_id must match article_id")
        selection = (self.scores, self.decision, self.tier, self.decision_reason)
        if not self.gate.passed and any(v is not None for v in selection):
            raise ValueError("an article excluded by a gate has no selection fields")
        if self.decision is None:
            if any(v is not None for v in selection):
                raise ValueError("selection fields require a decision")
            return self
        if self.scores is None or self.decision_reason is None:
            raise ValueError("a decision requires scores and decision_reason")
        if (self.tier is not None) != (self.decision is Decision.INCLUDED):
            raise ValueError("tier is required for included articles and forbidden otherwise")
        return self


class RunStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_FAILED = "partially_failed"
    """Finished with at least one failed stage, e.g. Judge or comment failed after publishing."""
    FAILED = "failed"
    ABORTED = "aborted"
    """Interrupted before the flow finished (timeout, cancellation)."""


ALLOWED_RUN_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = MappingProxyType(
    {
        RunStatus.RUNNING: frozenset(
            {RunStatus.SUCCEEDED, RunStatus.PARTIALLY_FAILED, RunStatus.FAILED, RunStatus.ABORTED}
        ),
        RunStatus.SUCCEEDED: frozenset(),
        RunStatus.PARTIALLY_FAILED: frozenset(),
        RunStatus.FAILED: frozenset(),
        RunStatus.ABORTED: frozenset(),
    }
)


class RunMetrics(ContractModel):
    run_id: RecordId
    status: RunStatus
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    stages: tuple[StageMetrics, ...] = ()
    sources: tuple[SourceMetrics, ...] = ()
    articles: tuple[ArticleMetrics, ...] = ()
    llm_calls: tuple[LLMCallMetrics, ...] = ()
    judge_findings: tuple[FindingMetrics, ...] = ()
    published_must_read_count: int = Field(default=0, ge=0, le=MUST_READ_MAX, strict=True)
    published_worth_knowing_count: int = Field(default=0, ge=0, le=WORTH_KNOWING_MAX, strict=True)

    @property
    def duration_ms(self) -> int | None:
        return _duration_ms(self.started_at, self.ended_at)

    @model_validator(mode="after")
    def _check_run(self) -> RunMetrics:
        for label, keys in (
            ("stages.stage", (s.stage for s in self.stages)),
            ("sources.source_id", (s.source_id for s in self.sources)),
            ("articles.article_id", (a.article_id for a in self.articles)),
            ("llm_calls.call_id", (c.call_id for c in self.llm_calls)),
            ("judge_findings.finding_id", (j.finding_id for j in self.judge_findings)),
        ):
            if first_duplicate(keys) is not None:
                raise ValueError(f"{label} must be unique")

        for tier, limit in ((Tier.MUST_READ, MUST_READ_MAX), (Tier.WORTH_KNOWING, WORTH_KNOWING_MAX)):
            if sum(article.tier is tier for article in self.articles) > limit:
                raise ValueError(f"at most {limit} {tier.value} articles")
        published = self.published_must_read_count or self.published_worth_knowing_count
        if published and not any(
            s.stage is StageName.PUBLISH and s.status is StageStatus.SUCCEEDED for s in self.stages
        ):
            raise ValueError("published counts require a succeeded publish stage")

        if (self.ended_at is None) != (self.status is RunStatus.RUNNING):
            raise ValueError("ended_at is required once the run is finished and forbidden while running")
        _check_order(self.started_at, self.ended_at)
        if self.status is RunStatus.RUNNING:
            return self

        if any(s.status not in TERMINAL_STAGE_STATUSES for s in self.stages):
            raise ValueError("a finished run must not leave pending or running stages")
        has_failed_stage = any(s.status is StageStatus.FAILED for s in self.stages)
        if self.status is RunStatus.SUCCEEDED and has_failed_stage:
            raise ValueError("a succeeded run must not contain failed stages")
        if self.status in (RunStatus.PARTIALLY_FAILED, RunStatus.FAILED) and not has_failed_stage:
            raise ValueError(f"a {self.status.value} run requires a failed stage")
        return self

    def finish(self, status: RunStatus, at: datetime) -> RunMetrics:
        if status not in ALLOWED_RUN_TRANSITIONS[self.status]:
            raise InvalidTransitionError(f"run {self.status.value} -> {status.value} is not allowed")
        return type(self).model_validate({**self.model_dump(), "status": status, "ended_at": at})
