"""The Judge's audit fixture: a Selector result with known problems planted in it.

The packets are the Selector's fixed set as #6 built it (`selector_support`,
without the cases #31 added) plus one article that repeats `dup_official` under
another title, so clustering missed it. Keeping the set fixed keeps each Judge
run comparable with the ones recorded before. The
decision below is one the Selector's own checks accept - every Evidence ID
exists and every 根拠 cites concrete evidence - yet it carries the problems the
Judge is there to catch:

- `harness_retry`: 何をしたか says completions nearly doubled; the source says 212 to 229 of 240
- `funding_news`: a funding round adopted for developers who build agents
- `numbers_no_conditions`: 根拠 adds a 20-point gain the source never states, and no 留保
- `hooks_repost`: the same PreToolUse hook as `dup_official`, adopted twice

`dup_official` itself is written faithfully and is the control for false
positives. `KNOWN_ISSUES` lists, for each planted problem, the articles the
finding may name and the categories that describe it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from digest_contracts import EvidencePacket, SelectorOutput, SourceKind
from digest_research import SourceLibrary
from selector_support import CASES, case
from selector_support import library as selector_library

REPOST = case(
    "hooks_repost",
    "How I stopped my coding agent from running rm -rf",
    source_id="zenn",
    kind=SourceKind.DISCOVERY,
    evidence=(
        ("statement", "Claude Code can stop a tool call before it runs with a PreToolUse hook."),
        ("config", '"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "./guard.sh"}]}]'),
    ),
    claims=(("what_happened", "著者は PreToolUse フックで Bash の実行前に確認を挟む設定を紹介した。", (0, 1), False, ()),),
    hours=3,
)

SELECTOR_CASES_BEFORE_31 = frozenset(
    {"harness_retry", "funding_news", "thin_post", "vendor_promo", "numbers_no_conditions",
     "context_essay", "dup_official", "dup_hn", "injected"}
)

PACKETS: tuple[EvidencePacket, ...] = (
    *(c.packet for c in CASES if c.packet.article_id in SELECTOR_CASES_BEFORE_31),
    REPOST.packet,
)

GOOD = {"practicality": 4, "specificity_reproducibility": 4, "novelty": 3, "source_reliability": 4, "reader_impact": 4, "read_original_value": 4}
MIDDLE = {"practicality": 3, "specificity_reproducibility": 3, "novelty": 3, "source_reliability": 3, "reader_impact": 3, "read_original_value": 3}
LOW = {k: 2 for k in GOOD}
LOWEST = {k: 1 for k in GOOD}


def _ids(article_id: str) -> list[str]:
    return [e.evidence_id for e in next(p for p in PACKETS if p.article_id == article_id).evidence]


def _included(article_id: str, reason: str, what: tuple[str, tuple[int, ...]], why: str, evidence: tuple[str, tuple[int, ...]], headline: str, scores: dict = GOOD) -> dict[str, Any]:
    ids = _ids(article_id)
    return {
        "article_id": article_id,
        "scores": scores,
        "decision_reason": reason,
        "entry": {
            "what_happened": {"text": what[0], "evidence_ids": [ids[i] for i in what[1]]},
            "why_read": why,
            "evidence": {"text": evidence[0], "evidence_ids": [ids[i] for i in evidence[1]]},
            "headline": headline,
        },
    }


DECISION: dict[str, Any] = {
    "must_read": [
        _included(
            "harness_retry",
            "再試行の上限と、その前後の完了数がそろっている。",
            ("著者はステージごとの再試行を3回に制限し、完了タスクがほぼ2倍になった。", (0, 2)),
            "再試行の上限をどう置くかを決める材料になる。",
            ("RetryBudget(max_attempts=3, per_stage=True) の設定と、240件を同じコミットで再実行した手順。", (1, 3)),
            "ステージごとの再試行の上限と完了数の変化",
        ),
    ],
    "worth_knowing": [
        _included(
            "dup_official",
            "公式が PreToolUse フックの設定例を示した。",
            ("Anthropic は PreToolUse フックでツール呼び出しを実行前に止められることを示した。", (0,)),
            "Coding Agent の危険な操作を実行前に止める仕組みを検討する材料になる。",
            ("Bash に対する PreToolUse フックの設定例。", (1,)),
            "PreToolUse フックでツール呼び出しを実行前に止める",
        ),
        _included(
            "funding_news",
            "評価額が大きく、業界の動きとして知っておく価値がある。",
            ("AI 企業が新たな資金調達を発表し、評価額は400億ドルとされた。", (0, 1)),
            "業界の資金の流れを把握できる。",
            ("評価額400億ドルという数字。", (1,)),
            "AI 企業の資金調達と評価額",
            MIDDLE,
        ),
        _included(
            "numbers_no_conditions",
            "計画ステップの作り直しと解決率の数字がある。",
            ("著者はコーディングエージェントの計画ステップを、テスト実行を明示する形に作り直した。", (0,)),
            "計画ステップの設計を見直す材料になる。",
            ("社内スイートでタスクの85%を解決し、作り直す前より20ポイント改善した。", (1,)),
            "計画ステップの作り直しと解決率",
            MIDDLE,
        ),
        _included(
            "hooks_repost",
            "PreToolUse フックの実用的な設定例がある。",
            ("著者は PreToolUse フックで Bash の実行前に確認を挟む設定を紹介した。", (0,)),
            "Coding Agent の危険な操作を止める設定をそのまま試せる。",
            ("Bash に対する PreToolUse フックの設定例。", (1,)),
            "PreToolUse フックで Bash の実行前に確認を挟む",
        ),
    ],
    "excluded": [
        {"article_id": "context_essay", "scores": MIDDLE, "decision_reason": "方針の話が中心で、測定条件が確認できない。"},
        {"article_id": "vendor_promo", "scores": LOW, "decision_reason": "宣伝で、10倍の測定条件が無い。"},
        {"article_id": "thin_post", "scores": LOWEST, "decision_reason": "具体的な根拠が無い。"},
        {"article_id": "injected", "scores": LOW, "decision_reason": "Agent 開発との関係が無い。"},
        {"article_id": "dup_hn", "scores": LOW, "decision_reason": "dup_official と同じ話題の議論。"},
    ],
    "duplicate_groups": [{"representative_id": "dup_official", "duplicate_ids": ["dup_hn"]}],
}

OUTPUT = SelectorOutput.model_validate(DECISION)


@dataclass(frozen=True)
class KnownIssue:
    label: str
    articles: frozenset[str]
    categories: frozenset[str]


KNOWN_ISSUES: tuple[KnownIssue, ...] = (
    KnownIssue("忠実性: 212→229件を「ほぼ2倍」", frozenset({"harness_retry"}), frozenset({"summary_faithfulness", "groundedness"})),
    KnownIssue("対象外: 資金調達の採用", frozenset({"funding_news"}), frozenset({"scope_fit", "recommendation_validity"})),
    KnownIssue("根拠: 本文に無い20ポイント改善", frozenset({"numbers_no_conditions"}), frozenset({"groundedness", "summary_faithfulness", "specificity"})),
    KnownIssue("重複: 同じフック設定を2件採用", frozenset({"hooks_repost", "dup_official"}), frozenset({"duplication"})),
)

CONTROL = "dup_official"
"""Written faithfully. A finding on it other than duplication is a false positive."""


def library() -> SourceLibrary:
    lib = selector_library()
    lib.add(REPOST.document)
    return lib
