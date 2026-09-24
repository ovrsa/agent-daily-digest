"""The Judge report as a commit comment, and its findings as metrics.

The comment is what a person reads once a week (Design Doc, Judge report and
human review). Each finding shows the assessment and the improvement under
separate headings, then the line the person answers with. Model text is
untrusted: it is collapsed to one line, capped, stripped of anything that
looks like a credential, and kept from opening HTML, so a comment carries only
a short quotation and the article URL - never a long repost or a secret.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from digest_contracts import AuditTargetKind, EvidencePacket, FindingMetrics, JudgeFinding, JudgeReport, Severity
from digest_observe import redact_secrets
from digest_render import inline, link

from .judge import JudgeResult

ANSWERS = ("妥当", "一部妥当", "不当", "判断不能")
"""What a person answers to each finding, in the order the design lists them."""

TEXT_MAX_CHARS = 400
"""Per problem and per fix. `source_evidence` is already capped at 300 by the contract."""

_SEVERITY_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
_KIND_LABELS = {
    AuditTargetKind.MUST_READ: "Must Read",
    AuditTargetKind.WORTH_KNOWING: "Worth Knowing",
    AuditTargetKind.BOUNDARY_EXCLUDED: "境界除外",
    AuditTargetKind.DUPLICATE_REPRESENTATIVE: "重複代表",
}
_TRUNCATED = "（以下省略）"


def finding_metrics(report: JudgeReport | None) -> tuple[FindingMetrics, ...]:
    """The classification of each finding, without its text, for `RunRecorder.record_findings`."""
    return () if report is None else tuple(finding.to_metrics() for finding in report.findings)


def render_report(result: JudgeResult, packets: Sequence[EvidencePacket]) -> str:
    """Markdown for the digest commit. A failed Judge renders as a short failure note."""
    by_id = {packet.article_id: packet for packet in packets}
    lines = ["## Judge レポート", "", f"- モデル: {result.model} / プロンプト: {result.prompt_version}", f"- 監査対象: {_targets_line(result)}"]
    if result.report is None:
        kind = result.error.kind.value if result.error is not None else "unknown"
        lines += [f"- 結果: 失敗（{kind}）。ダイジェストは公開済みで、この回の指摘は無い", ""]
        return "\n".join(lines)
    findings = sorted(result.report.findings, key=lambda f: _SEVERITY_ORDER[f.assessment.severity])
    lines.append(f"- 指摘: {_findings_line(findings)}")
    for finding in findings:
        lines += _finding_lines(finding, by_id)
    if findings:
        lines += [
            "",
            "### 回答の書き方",
            "",
            f"このコミットへ、指摘ごとに1行ずつ `ID: 回答` の形で書く。回答は {' / '.join(ANSWERS)} のどれか。理由は回答の後に続けてよい。",
            "",
            "```text",
            *(f"{finding.finding_id}: " for finding in findings),
            "```",
        ]
    lines.append("")
    return "\n".join(lines)


def _targets_line(result: JudgeResult) -> str:
    if not result.targets:
        return "0件"
    counts = {kind: sum(kind in target.kinds for target in result.targets) for kind in _KIND_LABELS}
    detail = "、".join(f"{label} {counts[kind]}" for kind, label in _KIND_LABELS.items() if counts[kind])
    return f"{len(result.targets)}件（{detail}）"


def _findings_line(findings: Sequence[JudgeFinding]) -> str:
    if not findings:
        return "0件"
    counts = {severity: sum(f.assessment.severity is severity for f in findings) for severity in _SEVERITY_ORDER}
    return f"{len(findings)}件（" + "、".join(f"{s.value} {n}" for s, n in counts.items() if n) + "）"


def _finding_lines(finding: JudgeFinding, packets: Mapping[str, EvidencePacket]) -> list[str]:
    assessment = finding.assessment
    packet = packets.get(finding.article_id)
    article = link(_text(packet.title), packet.canonical_url) if packet is not None else f"`{finding.article_id}`"
    return [
        "",
        f"### {finding.finding_id} [{assessment.severity.value}] {assessment.category.value}",
        "",
        f"対象: {article}（`{finding.article_id}`）",
        "",
        "評価",
        "",
        f"- 問題: {_text(assessment.problem)}",
        f"- 本文上の根拠: {_text(assessment.source_evidence)}",
        f"- 確信度: {assessment.confidence.value}",
        "",
        "改善候補",
        "",
        f"- 修正案: {_text(finding.improvement.suggested_fix)}",
    ]


def _text(value: str) -> str:
    text = inline(redact_secrets(value)).replace("<", "&lt;")
    if len(text) > TEXT_MAX_CHARS:
        text = text[: TEXT_MAX_CHARS - len(_TRUNCATED)] + _TRUNCATED
    return text
