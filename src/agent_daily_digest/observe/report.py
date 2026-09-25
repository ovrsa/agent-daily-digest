"""Presentation: a `RunSummary` as Markdown for the run log and the commit comment.

Only figures, identifiers and enum values are printed. A summary carries no
article text, so nothing here can quote one.
"""

from __future__ import annotations

from collections.abc import Iterable

from .summary import RoleUsage, RunSummary


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
