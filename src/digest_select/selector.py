"""The Selector: one structured call that scores every article and writes the entries.

The input is the Evidence Packets from research (#12), never full text. For a
packet whose research is `partial`, the paragraphs behind its claims are added
so a 留保 can be judged against the source; that is the only time the Selector
sees paragraphs, and Python decides it, not the model.

The model decides; Python then checks the decision against rules the JSON
Schema cannot hold. A decision that breaks one is rejected with
`OutputRejected` and retried within the policy, and when the retries run out
the Selector fails, which keeps the digest unpublished (Design Doc, Failure
policy):

- every article appears exactly once, and nothing else appears
- every Evidence ID an entry cites belongs to that article's packet
- the 根拠 line cites at least one concrete piece of evidence
- no article whose research is `insufficient`, and no supporting article of a
  research cluster, is adopted
- the entry text carries none of the artifacts the renderer bans, and each
  field stays within `ENTRY_TEXT_MAX` (a digest is read in five minutes)

A retry is told which rules the last decision broke, by location and rule
name only, appended after the unchanged prompt so the cached prefix still
applies. The rejected text itself is never sent back.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from pydantic import ValidationError

from digest_contracts import (
    CONCRETE_EVIDENCE_KINDS,
    ErrorRecord,
    EvidencePacket,
    IncludedArticle,
    LLMCallMetrics,
    LLMRole,
    ResearchStatus,
    SelectorOutput,
    ValidationIssue,
)
from digest_llm import StructuredRequest
from digest_observe import CallSpec, LLMResponse, OutputRejected, PricingTable, RetryPolicy, measured_call
from digest_render import forbidden_artifacts_in
from digest_research import SourceLibrary, render_packet

from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, selection_prompt

Invoker = Callable[[StructuredRequest], LLMResponse]

DEFAULT_RETRY = RetryPolicy(max_attempts=3)
"""Up to two retries, each told which rules the previous decision broke."""

ENTRY_TEXT_MAX: Mapping[str, int] = {"what_happened": 200, "why_read": 200, "evidence": 200, "caveat": 200, "headline": 40}
"""Characters per entry field. The contract has no cap; the published digest needs one.

The headline has to fit one line of the overview at the top of the digest."""

RULE_HINTS: Mapping[str, str] = {
    "missing_article": "入力の記事が must_read / worth_knowing / excluded のどこにも入っていない",
    "unknown_article": "入力に無い article_id を使った",
    "unknown_evidence_id": "その記事の evidence に無い ID を引いた",
    "evidence_not_concrete": "根拠の evidence_ids に code / config / number / comparison / failure / procedure の根拠が無い",
    "insufficient_research_included": "research が insufficient の記事を採用した",
    "supporting_article_included": "represented_by がある記事を採用した",
    "entry_too_long": "掲載文が長すぎる（what_happened、why_read、evidence、caveat は200字、headline は40字まで）",
}
"""What a rule name means, for the retry note. `forbidden_*` rules are explained by their own name."""

EXCERPT_CHARS_PER_PACKET = 1_500

CALL_ID = "selector-1"


@dataclass(frozen=True)
class SelectionResult:
    """The Selector's output with what produced it, for the renderer, the Judge and the metrics."""

    output: SelectorOutput | None
    model: str
    prompt_version: str
    metrics: LLMCallMetrics | None = None
    error: ErrorRecord | None = None

    @property
    def succeeded(self) -> bool:
        return self.output is not None


@dataclass
class Selector:
    invoke: Invoker
    pricing: PricingTable
    model: str
    policy: RetryPolicy = DEFAULT_RETRY
    record: Callable[[LLMCallMetrics], None] | None = None
    max_call_usd: float | None = 1.0
    excerpt_chars: int = EXCERPT_CHARS_PER_PACKET
    library: SourceLibrary = field(default_factory=SourceLibrary)

    def select(self, packets: Sequence[EvidencePacket]) -> SelectionResult:
        """Decide every packet. Nothing to decide is an empty output without a model call."""
        if not packets:
            empty = SelectorOutput(must_read=(), worth_knowing=(), excluded=())
            return SelectionResult(output=empty, model=self.model, prompt_version=PROMPT_VERSION)
        by_id = {packet.article_id: packet for packet in packets}
        request = StructuredRequest(
            model=self.model,
            system_prompt=SYSTEM_PROMPT,
            prompt=self.prompt(packets),
            schema=SelectorOutput.model_json_schema(),
            max_budget_usd=self.max_call_usd,
        )
        broken: list[ValidationIssue] = []

        def invoke(attempt: int) -> LLMResponse:
            if attempt == 1 or not broken:
                return self.invoke(request)
            return self.invoke(replace(request, prompt=request.prompt + retry_note(broken)))

        def parse(value: object) -> SelectorOutput:
            try:
                return checked(SelectorOutput.model_validate(value), by_id)
            except OutputRejected as exc:
                broken[:] = exc.issues
                raise
            except ValidationError as exc:
                broken[:] = ValidationIssue.from_validation_error(exc)
                raise

        outcome = measured_call(
            CallSpec(call_id=CALL_ID, role=LLMRole.SELECTOR, model=self.model, prompt_version=PROMPT_VERSION),
            invoke,
            parse,
            pricing=self.pricing,
            policy=self.policy,
            record=self.record,
        )
        return SelectionResult(
            output=outcome.value,
            model=self.model,
            prompt_version=PROMPT_VERSION,
            metrics=outcome.metrics,
            error=outcome.error,
        )

    def prompt(self, packets: Sequence[EvidencePacket]) -> str:
        blocks = []
        for packet in packets:
            block = render_packet(packet)
            if packet.status is ResearchStatus.PARTIAL:
                cited = [i for claim in packet.claims for i in claim.evidence_ids]
                block += (
                    "\n原文の該当段落（research が partial のため、主張の根拠の段落を添える）:\n"
                    + self.library.excerpt(cited, max_chars=self.excerpt_chars)
                )
            blocks.append(block)
        return selection_prompt(tuple(blocks))


def retry_note(issues: Sequence[ValidationIssue]) -> str:
    """The rules the last decision broke, by location and rule name. No rejected text."""
    lines = ["", "", "前回の出力は次の規則に違反したため受け付けなかった。すべて直して、全体を出し直す:"]
    for issue in issues:
        hint = RULE_HINTS.get(issue.type)
        lines.append(f"- {issue.loc}: {issue.type}" + (f"（{hint}）" if hint else ""))
    return "\n".join(lines)


def checked(output: SelectorOutput, packets: Mapping[str, EvidencePacket]) -> SelectorOutput:
    """Return `output` when it passes every rule, or raise `OutputRejected` naming each break."""
    issues = list(selection_issues(output, packets))
    if issues:
        raise OutputRejected(tuple(issues))
    return output


def selection_issues(output: SelectorOutput, packets: Mapping[str, EvidencePacket]) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    decided = set(output.article_ids)
    for bucket, articles in (("must_read", output.must_read), ("worth_knowing", output.worth_knowing), ("excluded", output.excluded)):
        for index, article in enumerate(articles):
            if article.article_id not in packets:
                issues.append(ValidationIssue(loc=f"{bucket}.{index}.article_id", type="unknown_article"))
    for article_id in packets:
        if article_id not in decided:
            issues.append(ValidationIssue(loc=f"articles.{article_id}", type="missing_article"))

    for bucket, articles in (("must_read", output.must_read), ("worth_knowing", output.worth_knowing)):
        for index, article in enumerate(articles):
            packet = packets.get(article.article_id)
            if packet is not None:
                issues += _included_issues(f"{bucket}.{index}", article, packet)

    issues += [
        ValidationIssue(loc=artifact.location, type=f"forbidden_{artifact.rule.value}")
        for artifact in forbidden_artifacts_in(output)
    ]
    return tuple(issues)


def _included_issues(at: str, article: IncludedArticle, packet: EvidencePacket) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if packet.represented_by is not None:
        issues.append(ValidationIssue(loc=f"{at}.article_id", type="supporting_article_included"))
    elif packet.status is ResearchStatus.INSUFFICIENT:
        issues.append(ValidationIssue(loc=f"{at}.article_id", type="insufficient_research_included"))
    entry = article.entry
    statements = [("what_happened", entry.what_happened), ("evidence", entry.evidence)]
    if entry.caveat is not None:
        statements.append(("caveat", entry.caveat))
    texts = {name: statement.text for name, statement in statements} | {"why_read": entry.why_read, "headline": entry.headline}
    for name, text in texts.items():
        if len(text) > ENTRY_TEXT_MAX[name]:
            issues.append(ValidationIssue(loc=f"{at}.entry.{name}", type="entry_too_long"))
    for name, statement in statements:
        for position, evidence_id in enumerate(statement.evidence_ids):
            if evidence_id not in packet.evidence_ids:
                issues.append(ValidationIssue(loc=f"{at}.entry.{name}.evidence_ids.{position}", type="unknown_evidence_id"))
    kinds = {evidence.evidence_id: evidence.kind for evidence in packet.evidence}
    if not any(kinds.get(i) in CONCRETE_EVIDENCE_KINDS for i in entry.evidence.evidence_ids):
        issues.append(ValidationIssue(loc=f"{at}.entry.evidence.evidence_ids", type="evidence_not_concrete"))
    return issues
