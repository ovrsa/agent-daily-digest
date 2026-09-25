"""The machine check for the sentence-level artifacts the Design Doc bans."""

from __future__ import annotations

import pytest

import render_factories as f
from agent_daily_digest.contracts import SelectorOutput
from agent_daily_digest.render import (
    RULE_DESCRIPTIONS,
    ForbiddenArtifactError,
    ForbiddenRule,
    check_forbidden_artifacts,
    forbidden_artifacts_in,
    render_digest,
)

BANNED = [
    (ForbiddenRule.FULLWIDTH_DASH, "設定を変えた—失敗率は下がった。"),
    (ForbiddenRule.FULLWIDTH_DASH, "設定を変えた―失敗率は下がった。"),
    (ForbiddenRule.FULLWIDTH_DASH, "3–5 件を採用した。"),
    (ForbiddenRule.FULLWIDTH_DASH, "入力－出力の対応を示した。"),
    (ForbiddenRule.DECORATIVE_EMOJI, "🔥 今日の注目リリース。"),
    (ForbiddenRule.DECORATIVE_EMOJI, "対応済み ✅ の項目を数えた。"),
    (ForbiddenRule.DECORATIVE_EMOJI, "⚠️ 設定に注意する。"),
    (ForbiddenRule.ABSENT_SUBJECT, "この構成が有効だと言われている。"),
    (ForbiddenRule.ABSENT_SUBJECT, "再試行は不要だとされている。"),
    (ForbiddenRule.ABSENT_SUBJECT, "この手法は有力だと考えられている。"),
    (ForbiddenRule.ABSENT_SUBJECT, "この分野は注目されている。"),
    (ForbiddenRule.ABSENT_SUBJECT, "後継の実装が期待されている。"),
    (ForbiddenRule.ABSENT_SUBJECT, "設計の是非が議論を呼んでいる。"),
    (ForbiddenRule.ABSTRACT_PRAISE, "画期的な手法を示した。"),
    (ForbiddenRule.ABSTRACT_PRAISE, "革新的な構成に変えた。"),
    (ForbiddenRule.ABSTRACT_PRAISE, "圧倒的な速度を記録した。"),
    (ForbiddenRule.ABSTRACT_PRAISE, "これはまさにハーネスの設計問題だ。"),
    (ForbiddenRule.ABSTRACT_PRAISE, "注目に値する結果が出た。"),
    (ForbiddenRule.HYPE, "運用者は必見の内容。"),
    (ForbiddenRule.HYPE, "この変更は見逃せない。"),
    (ForbiddenRule.HYPE, "衝撃的な結果が出た。"),
    (ForbiddenRule.HYPE, "開発体験が激変する。"),
    (ForbiddenRule.HYPE, "ゲームチェンジャーになる。"),
    (ForbiddenRule.HYPE, "This is a game-changer for harness authors."),
    (ForbiddenRule.MECHANICAL_CONTRAST, "問題はモデルではなく、ハーネスの設計だ。"),
    (ForbiddenRule.MECHANICAL_CONTRAST, "これは単なる速度改善ではない。"),
]

ALLOWED = [
    "スコープは 3 件から 5 件に増えた。",
    "変更前後の失敗率は 12% と 4% だった。",
    "入力 -> 出力 の対応を表にしている。",
    "入力 → 出力 の変換を表にしている。",
    "権限スコープが実装されている。",
    "設定ファイルは `config.json` に置かれている。",
    "著者は再試行の上限を 3 回に設定したと書いている。",
    "一方の条件では失敗率が上がった。",
    "ASCII hyphens -- and --- are untouched.",
    "採用は Must Read が 3 件、Worth Knowing が 2 件だった。",
    # `だけでなく` は機械的な二項対比の型であると同時に事実の列挙にも出る。
    # 検出は公開を止めるので、このルールでは見ない(文体の指摘は Judge #8)。
    "この変更は CLI だけでなく SDK にも入った。",
    "計測対象は Sonnet だけでなく Haiku も含む。",
    "速度だけでなく、精度も上がった。",
    "評価は ✓ と ✗ で表されている。",
]

CHECK_MARKS = "\u2713\u2714\u2717\u2718"
"""`✓✔✗✘`. 比較表の記号として本文に出るので、装飾絵文字から外してある。"""

NEIGHBOURING_EMOJI = "\u2705\u2712\u2715\u2716\u2719"
"""`✅✒✕✖✙`. 外した範囲が広がっていないことを両隣で押さえる。"""


@pytest.mark.parametrize(("rule", "text"), BANNED, ids=[t for _, t in BANNED])
def test_a_banned_artifact_is_reported(rule: ForbiddenRule, text: str) -> None:
    findings = check_forbidden_artifacts(text)
    assert rule in {finding.rule for finding in findings}


@pytest.mark.parametrize("text", ALLOWED)
def test_ordinary_technical_prose_is_left_alone(text: str) -> None:
    assert check_forbidden_artifacts(text) == ()


@pytest.mark.parametrize("mark", CHECK_MARKS)
def test_a_check_or_ballot_mark_is_not_decoration(mark: str) -> None:
    assert check_forbidden_artifacts(f"対応は {mark} で示されている。") == ()


@pytest.mark.parametrize("char", NEIGHBOURING_EMOJI)
def test_the_symbols_next_to_the_check_marks_stay_decoration(char: str) -> None:
    findings = check_forbidden_artifacts(f"対応は {char} で示されている。")
    assert [finding.rule for finding in findings] == [ForbiddenRule.DECORATIVE_EMOJI]


def test_every_rule_has_a_description_and_a_case_covering_it() -> None:
    assert set(RULE_DESCRIPTIONS) == set(ForbiddenRule)
    assert {rule for rule, _ in BANNED} == set(ForbiddenRule)


def test_findings_carry_the_location_and_are_ordered_by_position() -> None:
    findings = check_forbidden_artifacts(
        "画期的な結果—必見の内容。", location="must_read[0].entry.why_read"
    )
    assert [finding.rule for finding in findings] == [
        ForbiddenRule.ABSTRACT_PRAISE,
        ForbiddenRule.FULLWIDTH_DASH,
        ForbiddenRule.HYPE,
    ]
    assert [finding.start for finding in findings] == [0, 6, 7]
    assert {finding.location for finding in findings} == {"must_read[0].entry.why_read"}


def test_the_check_reports_and_never_rewrites() -> None:
    text = "画期的な結果。"
    check_forbidden_artifacts(text)
    assert text == "画期的な結果。"


def test_the_fixture_digest_is_clean() -> None:
    assert forbidden_artifacts_in(f.selector_output()) == ()


def test_the_renderer_refuses_to_produce_a_digest_with_a_banned_artifact() -> None:
    payload = f.selector_payload()
    payload["must_read"] = [
        f.included_payload(why_read="これは画期的な変更だ。"),
        *payload["must_read"][1:],
    ]
    with pytest.raises(ForbiddenArtifactError) as raised:
        render_digest(SelectorOutput.model_validate(payload), f.articles(), f.DIGEST_DATE)
    findings = raised.value.findings
    assert [finding.rule for finding in findings] == [ForbiddenRule.ABSTRACT_PRAISE]
    assert findings[0].location == "must_read[0].entry.why_read"


@pytest.mark.parametrize(
    "field", ["what_happened", "why_read", "evidence", "caveat", "headline"]
)
def test_every_selector_authored_field_is_checked(field: str) -> None:
    banned = "画期的な結果が出た。"
    plain = field in ("why_read", "headline")
    value = banned if plain else {"text": banned, "evidence_ids": ["x1"]}
    payload = f.selector_payload()
    payload["must_read"] = [f.included_payload(**{field: value}), *payload["must_read"][1:]]
    findings = forbidden_artifacts_in(SelectorOutput.model_validate(payload))
    suffix = "" if plain else ".text"
    assert findings[0].location == f"must_read[0].entry.{field}{suffix}"


def test_a_quoted_title_is_not_judged_as_our_prose() -> None:
    """An em dash in an English source title is the publisher's, not the digest's."""
    articles = f.articles()
    articles["a001"] = f.normalized_article(title="Plan and act — a split that worked 🔥")
    rendered = f.render(articles=articles)
    assert rendered is not None
    assert "Plan and act — a split that worked 🔥" in rendered


def test_the_renderer_fixed_wording_passes_its_own_check() -> None:
    rendered = f.render()
    assert rendered is not None
    ours = [
        line
        for line in rendered.splitlines()
        if not line.startswith("### ") and not line.startswith("ソース: ")
    ]
    assert check_forbidden_artifacts("\n".join(ours)) == ()
