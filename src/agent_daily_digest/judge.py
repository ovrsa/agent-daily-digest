"""The Judge: an audit of the Selector's result, in a call of its own.

Context separation is structural. `Judge.audit` takes a `SelectorOutput`, the
Evidence Packets and the source library, and nothing else: there is no
parameter that could carry the Selector's prompt, its attempts or its metrics.
What the Judge reads about a decision is what the Selector wrote down for
readers - tier and position, the six scores, the decision reason and the entry -
next to the same research the Selector read and the source paragraphs the
entry cites. The call is a new `query()` with the Judge's own system prompt,
so no conversation is shared.

A failed model call, or a report that stays invalid after the retry, is a
`JudgeResult` with `error` set rather than an exception, so the caller keeps
the digest published (Design Doc, Failure policy). Any `SelectorOutput` the
contract accepts can be audited, including one no Selector checked: an Evidence
ID the source library cannot resolve is shown as not found. Only a bug raises,
and the caller's Judge stage has to contain it. A report that names an article
outside the audit targets is rejected and retried.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from pydantic import ValidationError

from agent_daily_digest.content.text import (
    HEADER,
    UNTRUSTED_CLOSE,
    UNTRUSTED_OPEN,
    escape_untrusted,
)
from agent_daily_digest.contracts.base import ErrorRecord, ValidationIssue
from agent_daily_digest.contracts.editorial import (
    BOUNDARY_EXCLUDED_AUDIT_MAX,
    AuditTargetKind,
    AxisScores,
    CaveatStatement,
    ExcludedArticle,
    FactStatement,
    FindingMetrics,
    IncludedArticle,
    JudgeFinding,
    JudgeReport,
    SelectorOutput,
    Severity,
    Tier,
)
from agent_daily_digest.contracts.metrics import LLMCallMetrics, LLMRole
from agent_daily_digest.contracts.research import EvidencePacket
from agent_daily_digest.judge_prompt import PROMPT_VERSION, SYSTEM_PROMPT, audit_prompt
from agent_daily_digest.llm.call import (
    CallSpec,
    LLMResponse,
    OutputRejected,
    RetryPolicy,
    StructuredRequest,
    measured_call,
)
from agent_daily_digest.llm.pricing import PricingTable
from agent_daily_digest.observe.store import redact_secrets
from agent_daily_digest.render.digest import inline, link
from agent_daily_digest.research.evidence import SourceLibrary, render_packet

Invoker = Callable[[StructuredRequest], LLMResponse]

DEFAULT_RETRY = RetryPolicy(max_attempts=2)
"""One retry. A Judge that fails twice is recorded as failed; the digest stays published."""

RULE_HINTS: Mapping[str, str] = {
    "finding_for_non_target": "監査対象に無い article_id への指摘。指摘は監査対象の記事にだけ付ける",
}

EXCERPT_CHARS_PER_TARGET = 2_000
CALL_ID = "judge-1"


@dataclass(frozen=True)
class JudgeResult:
    report: JudgeReport | None
    targets: tuple[AuditTarget, ...]
    model: str
    prompt_version: str
    metrics: LLMCallMetrics | None = None
    error: ErrorRecord | None = None

    @property
    def succeeded(self) -> bool:
        return self.report is not None


@dataclass
class Judge:
    invoke: Invoker
    pricing: PricingTable
    model: str
    policy: RetryPolicy = DEFAULT_RETRY
    record: Callable[[LLMCallMetrics], None] | None = None
    max_call_usd: float | None = 1.0
    excerpt_chars: int = EXCERPT_CHARS_PER_TARGET
    library: SourceLibrary = field(default_factory=SourceLibrary)

    def audit(self, output: SelectorOutput, packets: Sequence[EvidencePacket]) -> JudgeResult:
        """Audit the targets `select_targets` picks. Nothing to audit is an empty report without a call."""
        targets = select_targets(output)
        if not targets:
            return JudgeResult(report=JudgeReport(), targets=(), model=self.model, prompt_version=PROMPT_VERSION)
        request = StructuredRequest(
            model=self.model,
            system_prompt=SYSTEM_PROMPT,
            prompt=self.prompt(output, packets, targets),
            schema=JudgeReport.model_json_schema(),
            max_budget_usd=self.max_call_usd,
        )
        allowed = {target.article_id for target in targets}
        broken: list[ValidationIssue] = []

        def invoke(attempt: int) -> LLMResponse:
            if attempt == 1 or not broken:
                return self.invoke(request)
            return self.invoke(replace(request, prompt=request.prompt + retry_note(broken)))

        def parse(value: object) -> JudgeReport:
            try:
                return checked(JudgeReport.model_validate(value), allowed)
            except OutputRejected as exc:
                broken[:] = exc.issues
                raise
            except ValidationError as exc:
                broken[:] = ValidationIssue.from_validation_error(exc)
                raise

        outcome = measured_call(
            CallSpec(call_id=CALL_ID, role=LLMRole.JUDGE, model=self.model, prompt_version=PROMPT_VERSION),
            invoke,
            parse,
            pricing=self.pricing,
            policy=self.policy,
            record=self.record,
        )
        return JudgeResult(
            report=outcome.value,
            targets=targets,
            model=self.model,
            prompt_version=PROMPT_VERSION,
            metrics=outcome.metrics,
            error=outcome.error,
        )

    def prompt(self, output: SelectorOutput, packets: Sequence[EvidencePacket], targets: Sequence[AuditTarget]) -> str:
        by_id = {packet.article_id: packet for packet in packets}
        blocks = [self._target_block(output, by_id, target) for target in targets]
        return audit_prompt([target.article_id for target in targets], blocks)

    def _target_block(self, output: SelectorOutput, packets: Mapping[str, EvidencePacket], target: AuditTarget) -> str:
        parts = ["\n".join([UNTRUSTED_OPEN, HEADER, *_decision_lines(output, target), UNTRUSTED_CLOSE])]
        packet = packets.get(target.article_id)
        if packet is None:
            parts.append(f"（{target.article_id} の根拠の地図は渡されていない）")
            return "\n".join(parts)
        parts.append(render_packet(packet))
        # The entry's citations come first so the character cap trims the claims' evidence, not them.
        cited = [*_cited(output, target.article_id), *(i for claim in packet.claims for i in claim.evidence_ids)]
        if cited:
            parts.append(
                "掲載文と主張が引いた根拠の原文段落:\n" + self.library.excerpt(cited, max_chars=self.excerpt_chars)
            )
        for group in output.duplicate_groups:
            if group.representative_id == target.article_id:
                for member in group.duplicate_ids:
                    if member in packets:
                        parts.append(f"重複として除外された {member} の根拠の地図:\n" + render_packet(packets[member]))
        return "\n".join(parts)


def retry_note(issues: Sequence[ValidationIssue]) -> str:
    """The rules the last report broke, by location and rule name. No rejected text."""
    lines = ["", "", "前回の出力は次の規則に違反したため受け付けなかった。すべて直して、全体を出し直す:"]
    for issue in issues:
        hint = RULE_HINTS.get(issue.type)
        lines.append(f"- {issue.loc}: {issue.type}" + (f"（{hint}）" if hint else ""))
    return "\n".join(lines)


def checked(report: JudgeReport, allowed: set[str]) -> JudgeReport:
    """Return `report` when every finding names an audit target, or raise `OutputRejected`."""
    issues = tuple(
        ValidationIssue(loc=f"findings.{index}.article_id", type="finding_for_non_target")
        for index, finding in enumerate(report.findings)
        if finding.article_id not in allowed
    )
    if issues:
        raise OutputRejected(issues)
    return report


def _decision_lines(output: SelectorOutput, target: AuditTarget) -> list[str]:
    """What the Selector wrote down about one article: placement, scores, reason and entry."""
    placement, article = _placement(output, target.article_id)
    scores = ", ".join(f"{name}={value}" for name, value in article.scores.model_dump().items())
    lines = [
        f"audit_target: {target.article_id} ({', '.join(kind.value for kind in target.kinds)})",
        f"editor_decision: {placement}",
        f"editor_scores: {scores}",
        f"editor_reason: {escape_untrusted(article.decision_reason)}",
    ]
    if isinstance(article, IncludedArticle):
        entry = article.entry
        lines += [
            "entry:",
            f"- what_happened: {_statement(entry.what_happened)}",
            f"- why_read: {escape_untrusted(entry.why_read)}",
            f"- evidence: {_statement(entry.evidence)}",
        ]
        if entry.caveat is not None:
            lines.append(f"- caveat: {_statement(entry.caveat)}")
        lines.append(f"- headline: {escape_untrusted(entry.headline)}")
    return lines


def _placement(output: SelectorOutput, article_id: str) -> tuple[str, IncludedArticle | ExcludedArticle]:
    for tier, bucket in ((Tier.MUST_READ, output.must_read), (Tier.WORTH_KNOWING, output.worth_knowing)):
        for position, article in enumerate(bucket, start=1):
            if article.article_id == article_id:
                return f"included / {tier.value} #{position} of {len(bucket)}", article
    for article in output.excluded:
        if article.article_id == article_id:
            return "excluded", article
    raise KeyError(article_id)


def _statement(statement: FactStatement | CaveatStatement) -> str:
    ids = ", ".join(statement.evidence_ids) or "-"
    return f"{escape_untrusted(statement.text)} [evidence: {escape_untrusted(ids)}]"


def _cited(output: SelectorOutput, article_id: str) -> list[str]:
    for article in output.included:
        if article.article_id == article_id:
            entry = article.entry
            statements = [entry.what_happened, entry.evidence, *([entry.caveat] if entry.caveat else [])]
            return [i for statement in statements for i in statement.evidence_ids]
    return []


@dataclass(frozen=True)
class AuditTarget:
    article_id: str
    kinds: tuple[AuditTargetKind, ...]


def boundary_score(scores: AxisScores) -> tuple[int, int, int]:
    total = (
        scores.practicality
        + scores.specificity_reproducibility
        + scores.novelty
        + scores.source_reliability
        + scores.reader_impact
        + scores.read_original_value
    )
    return (total, scores.read_original_value, scores.reader_impact)


def select_targets(output: SelectorOutput) -> tuple[AuditTarget, ...]:
    kinds: dict[str, list[AuditTargetKind]] = {}

    def add(article_id: str, kind: AuditTargetKind) -> None:
        kinds.setdefault(article_id, [])
        if kind not in kinds[article_id]:
            kinds[article_id].append(kind)

    for article in output.must_read:
        add(article.article_id, AuditTargetKind.MUST_READ)
    for article in output.worth_knowing:
        add(article.article_id, AuditTargetKind.WORTH_KNOWING)
    grouped = {i for group in output.duplicate_groups for i in group.duplicate_ids}
    candidates = [a for a in output.excluded if a.article_id not in grouped]
    for article in sorted(candidates, key=_boundary_key)[:BOUNDARY_EXCLUDED_AUDIT_MAX]:
        add(article.article_id, AuditTargetKind.BOUNDARY_EXCLUDED)
    for group in output.duplicate_groups:
        add(group.representative_id, AuditTargetKind.DUPLICATE_REPRESENTATIVE)
    return tuple(AuditTarget(article_id, tuple(k)) for article_id, k in kinds.items())


def _boundary_key(article: ExcludedArticle) -> tuple[int, int, int, str]:
    total, original, impact = boundary_score(article.scores)
    return (-total, -original, -impact, article.article_id)


__all__ = [
    "ANSWERS",
    "CALL_ID",
    "COMMENT_FINDINGS_MAX",
    "DEFAULT_RETRY",
    "EXCERPT_CHARS_PER_TARGET",
    "PROMPT_VERSION",
    "RULE_HINTS",
    "SYSTEM_PROMPT",
    "TEXT_MAX_CHARS",
    "AuditTarget",
    "Invoker",
    "Judge",
    "JudgeResult",
    "audit_prompt",
    "boundary_score",
    "checked",
    "finding_metrics",
    "render_report",
    "retry_note",
    "select_targets",
]


ANSWERS = ("妥当", "一部妥当", "不当", "判断不能")
"""What a person answers to each finding, in the order the design lists them."""

TEXT_MAX_CHARS = 400
"""Per problem and per fix. `source_evidence` is already capped at 300 by the contract."""

COMMENT_FINDINGS_MAX = 20
"""Findings shown in one comment, most severe first. The rest are counted, and `finding_metrics` keeps all."""

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
    lines = [
        "## Judge レポート",
        "",
        f"- モデル: {result.model} / プロンプト: {result.prompt_version}",
        f"- 監査対象: {_targets_line(result)}",
    ]
    if result.report is None:
        kind = result.error.kind.value if result.error is not None else "unknown"
        lines += [f"- 結果: 失敗（{kind}）。ダイジェストは公開済みで、この回の指摘は無い", ""]
        return "\n".join(lines)
    findings = sorted(result.report.findings, key=lambda f: _SEVERITY_ORDER[f.assessment.severity])
    shown, omitted = findings[:COMMENT_FINDINGS_MAX], findings[COMMENT_FINDINGS_MAX:]
    lines.append(f"- 指摘: {_findings_line(findings)}")
    for finding in shown:
        lines += _finding_lines(finding, by_id)
    if omitted:
        lines += [
            "",
            f"ほかに {_findings_line(omitted)} の指摘は、コメントの大きさを抑えるため載せていない。分類は実行メトリクスに残る。",
        ]
    if shown:
        lines += [
            "",
            "### 回答の書き方",
            "",
            f"このコミットへ、指摘ごとに1行ずつ `ID: 回答` の形で書く。回答は {' / '.join(ANSWERS)} のどれか。理由は回答の後に続けてよい。",
            "",
            "```text",
            *(f"{finding.finding_id}: " for finding in shown),
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
