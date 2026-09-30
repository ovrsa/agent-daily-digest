"""Aggregation: one `RunMetrics` reduced to the figures a reviewer reads.

This module computes; it does not format. `report.py` renders a `RunSummary`
and never looks at `RunMetrics` itself, so the figures are defined in one place.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from agent_daily_digest.contracts import (
    AttemptStatus,
    Confidence,
    Decision,
    ErrorKind,
    LLMRole,
    RunMetrics,
    RunStatus,
    Severity,
    SourceFetchStatus,
    StageName,
    StageStatus,
    Tier,
)
from collections.abc import Iterable



@dataclass(frozen=True)
class StageLine:
    stage: StageName
    status: StageStatus
    duration_ms: int | None
    error_kind: ErrorKind | None


@dataclass(frozen=True)
class RoleUsage:
    """Every call one role made in a run. Token and cost totals are `None` when any attempt lacked them."""

    role: LLMRole
    models: tuple[str, ...]
    prompt_versions: tuple[str, ...]
    calls: int
    failed_calls: int
    attempts: int
    retries: int
    validation_failures: int
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    duration_ms: int


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    status: RunStatus
    started_at: datetime
    duration_ms: int | None
    stages: tuple[StageLine, ...]
    sources_succeeded: int
    sources_failed: tuple[tuple[str, ErrorKind], ...]
    items_collected: int
    articles_recorded: int
    gate_passed: int
    gate_exclusions: tuple[tuple[str, int], ...]
    """Exclusion reason and count, most frequent first."""
    extracted_chars_total: int
    selected_must_read: int
    selected_worth_knowing: int
    selector_excluded: int
    published_must_read: int
    published_worth_knowing: int
    llm: tuple[RoleUsage, ...]
    total_cost_usd: float | None
    findings_by_severity: tuple[tuple[Severity, int], ...]
    findings_by_confidence: tuple[tuple[Confidence, int], ...]


def summarize(run: RunMetrics) -> RunSummary:
    exclusions = Counter(
        reason.value for article in run.articles for reason in article.gate.exclusion_reasons
    )
    roles = tuple(_role_usage(run, role) for role in LLMRole if any(c.role is role for c in run.llm_calls))
    costs = [usage.cost_usd for usage in roles]
    severity = Counter(finding.severity for finding in run.judge_findings)
    confidence = Counter(finding.confidence for finding in run.judge_findings)
    return RunSummary(
        run_id=run.run_id,
        status=run.status,
        started_at=run.started_at,
        duration_ms=run.duration_ms,
        stages=tuple(
            StageLine(
                stage=stage.stage,
                status=stage.status,
                duration_ms=stage.duration_ms,
                error_kind=stage.error.kind if stage.error else None,
            )
            for stage in run.stages
        ),
        sources_succeeded=sum(s.status is SourceFetchStatus.SUCCEEDED for s in run.sources),
        sources_failed=tuple(
            (s.source_id, s.failure.kind) for s in run.sources if s.failure is not None
        ),
        items_collected=sum(s.item_count for s in run.sources),
        articles_recorded=len(run.articles),
        gate_passed=sum(article.gate.passed for article in run.articles),
        gate_exclusions=tuple(sorted(exclusions.items(), key=lambda pair: (-pair[1], pair[0]))),
        extracted_chars_total=sum(a.char_count or 0 for a in run.articles),
        selected_must_read=sum(a.tier is Tier.MUST_READ for a in run.articles),
        selected_worth_knowing=sum(a.tier is Tier.WORTH_KNOWING for a in run.articles),
        selector_excluded=sum(a.decision is Decision.EXCLUDED for a in run.articles),
        published_must_read=run.published_must_read_count,
        published_worth_knowing=run.published_worth_knowing_count,
        llm=roles,
        total_cost_usd=None if not costs or None in costs else round(sum(costs), 6),  # type: ignore[arg-type]
        findings_by_severity=tuple((level, severity[level]) for level in Severity if severity[level]),
        findings_by_confidence=tuple((level, confidence[level]) for level in Confidence if confidence[level]),
    )


def _role_usage(run: RunMetrics, role: LLMRole) -> RoleUsage:
    calls = [call for call in run.llm_calls if call.role is role]
    attempts = [attempt for call in calls for attempt in call.attempts]
    inputs = [call.total_input_tokens for call in calls]
    outputs = [call.total_output_tokens for call in calls]
    costs = [call.total_cost_usd for call in calls]
    return RoleUsage(
        role=role,
        models=tuple(sorted({call.model for call in calls})),
        prompt_versions=tuple(sorted({call.prompt_version for call in calls})),
        calls=len(calls),
        failed_calls=sum(not call.succeeded for call in calls),
        attempts=len(attempts),
        retries=sum(call.retry_count for call in calls),
        validation_failures=sum(
            a.status is AttemptStatus.FAILED and a.error is not None and a.error.kind is ErrorKind.VALIDATION
            for a in attempts
        ),
        input_tokens=None if None in inputs else sum(inputs),  # type: ignore[arg-type]
        output_tokens=None if None in outputs else sum(outputs),  # type: ignore[arg-type]
        cost_usd=None if None in costs else round(sum(costs), 6),  # type: ignore[arg-type]
        duration_ms=sum(a.duration_ms for a in attempts),
    )


def render_summary(summary: RunSummary) -> str:
    lines = [
        f"### 実行メトリクス `{summary.run_id}`",
        "",
        f"- 状態: {summary.status.value}",
        f"- 開始: {summary.started_at.isoformat()}",
        f"- 所要時間: {_ms(summary.duration_ms)}",
        f"- ソース: 成功 {summary.sources_succeeded} / 失敗 {len(summary.sources_failed)}"
        + _failed_sources(summary),
        f"- 収集件数: {summary.items_collected}、ゲート通過: {summary.gate_passed}"
        f" / 記録 {summary.articles_recorded}、抽出文字数合計: {summary.extracted_chars_total}",
        f"- ゲート除外理由: {_pairs(summary.gate_exclusions)}",
        f"- Selector: Must Read {summary.selected_must_read}、Worth Knowing {summary.selected_worth_knowing}、"
        f"除外 {summary.selector_excluded}",
        f"- 掲載: Must Read {summary.published_must_read}、Worth Knowing {summary.published_worth_knowing}",
        f"- 推定コスト合計（定価換算）: {_usd(summary.total_cost_usd)}",
        f"- Judge 指摘: 重大度 {_pairs((k.value, v) for k, v in summary.findings_by_severity)}、"
        f"確信度 {_pairs((k.value, v) for k, v in summary.findings_by_confidence)}",
        "",
        "| 工程 | 状態 | 所要時間 | エラー種別 |",
        "|---|---|---|---|",
    ]
    for stage in summary.stages:
        kind = stage.error_kind.value if stage.error_kind else "-"
        lines.append(f"| {stage.stage.value} | {stage.status.value} | {_ms(stage.duration_ms)} | {kind} |")
    if summary.llm:
        lines += [
            "",
            "| LLM | モデル | プロンプト版 | 呼び出し | 失敗 | 再試行 | 検証失敗 | 入力 token | 出力 token | 推定コスト |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        lines += [_role_row(usage) for usage in summary.llm]
    return "\n".join(lines) + "\n"


def _role_row(usage: RoleUsage) -> str:
    return (
        f"| {usage.role.value} | {', '.join(usage.models)} | {', '.join(usage.prompt_versions)} "
        f"| {usage.calls} | {usage.failed_calls} | {usage.retries} | {usage.validation_failures} "
        f"| {_count(usage.input_tokens)} | {_count(usage.output_tokens)} | {_usd(usage.cost_usd)} |"
    )


def _failed_sources(summary: RunSummary) -> str:
    if not summary.sources_failed:
        return ""
    return "（" + "、".join(f"{source}: {kind.value}" for source, kind in summary.sources_failed) + "）"


def _pairs(pairs: Iterable[tuple[object, int]]) -> str:
    text = "、".join(f"{key} {value}" for key, value in pairs)
    return text or "なし"


def _ms(value: int | None) -> str:
    return "-" if value is None else f"{value / 1000:.1f}s"


def _usd(value: float | None) -> str:
    return "不明" if value is None else f"{value:.4f} USD"


def _count(value: int | None) -> str:
    return "不明" if value is None else f"{value:,}"
