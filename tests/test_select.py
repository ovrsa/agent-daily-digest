"""The Selector: decisions the model makes, and the rules Python holds it to."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from digest_contracts import ErrorKind, LLMCallMetrics, LLMRole, SelectorOutput
from digest_observe import RetryPolicy
from digest_select import PROMPT_VERSION, SYSTEM_PROMPT, Selector, selection_issues
from observe_support import PRICING
from research_support import ScriptedModel
from selector_support import EXPECTED, PACKETS, library, packet

SCORES = {"practicality": 4, "specificity_reproducibility": 4, "novelty": 3, "source_reliability": 4, "reader_impact": 4, "read_original_value": 4}
LOW = {k: 2 for k in SCORES}


def entry(article_id: str, *, what: tuple[int, ...] = (0,), evidence: tuple[int, ...] = (1,), caveat: dict | None = None) -> dict:
    ids = [e.evidence_id for e in packet(article_id).evidence]
    data = {
        "what_happened": {"text": "著者はステージごとの再試行を3回に制限した。", "evidence_ids": [ids[i] for i in what]},
        "why_read": "再試行の上限をどう置くかを決める材料になる。",
        "evidence": {"text": "RetryBudget の設定と、212件から229件への変化。", "evidence_ids": [ids[i] for i in evidence]},
    }
    if caveat is not None:
        data["caveat"] = caveat
    return data


def decision(**changes: Any) -> dict:
    """A decision that passes every rule, following `EXPECTED` with both `either` cases excluded."""
    data = {
        "must_read": [{"article_id": "harness_retry", "scores": SCORES, "decision_reason": "再現手順と数値がそろう。", "entry": entry("harness_retry")}],
        "worth_knowing": [{"article_id": "dup_official", "scores": SCORES, "decision_reason": "公式の設定例がある。", "entry": entry("dup_official")}],
        "excluded": [
            {"article_id": a, "scores": LOW, "decision_reason": "対象外か根拠が弱い。"}
            for a, expected in EXPECTED.items()
            if expected != "include"
        ],
        "duplicate_groups": [{"representative_id": "dup_official", "duplicate_ids": ["dup_hn"]}],
    }
    data.update(changes)
    return data


def selector(model: ScriptedModel, **kwargs) -> Selector:
    kwargs.setdefault("policy", RetryPolicy(max_attempts=3, backoff_seconds=0))
    return Selector(invoke=model, pricing=PRICING, model="claude-sonnet-5", library=library(), **kwargs)


def issue_types(data: dict) -> set[str]:
    by_id = {p.article_id: p for p in PACKETS}
    return {issue.type for issue in selection_issues(SelectorOutput.model_validate(data), by_id)}


# -- a valid decision -------------------------------------------------------------


def test_a_valid_decision_is_returned_with_its_model_and_prompt_version() -> None:
    calls: list[LLMCallMetrics] = []
    result = selector(ScriptedModel(decision()), record=calls.append).select(PACKETS)

    assert result.succeeded and result.error is None
    assert [a.article_id for a in result.output.must_read] == ["harness_retry"]
    assert (result.model, result.prompt_version) == ("claude-sonnet-5", PROMPT_VERSION)
    assert result.metrics.role is LLMRole.SELECTOR and calls == [result.metrics]
    assert result.output.excluded[0].decision_reason  # the reason travels to later stages


def test_adopting_nothing_is_a_valid_decision() -> None:
    nothing = decision(
        must_read=[],
        worth_knowing=[],
        excluded=[{"article_id": p.article_id, "scores": LOW, "decision_reason": "今日は該当なし。"} for p in PACKETS],
        duplicate_groups=[],
    )
    result = selector(ScriptedModel(nothing)).select(PACKETS)
    assert result.succeeded and result.output.included == ()


def test_no_packets_means_no_model_call() -> None:
    model = ScriptedModel()
    result = selector(model).select(())
    assert result.succeeded and result.output.article_ids == () and model.requests == []


def test_a_caveat_is_optional_and_may_cite_no_evidence() -> None:
    data = decision()
    data["must_read"][0]["entry"] = entry("harness_retry", caveat={"text": "測定は1回ずつで、ばらつきは書かれていない。", "evidence_ids": []})
    assert issue_types(data) == set()
    del data["must_read"][0]["entry"]["caveat"]
    assert issue_types(data) == set()


# -- rules the schema cannot hold -------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "issue"),
    [
        (lambda d: d["excluded"].pop(), "missing_article"),
        (lambda d: d["excluded"].append({"article_id": "made_up", "scores": LOW, "decision_reason": "x"}), "unknown_article"),
        (lambda d: d["must_read"][0]["entry"]["what_happened"].update(evidence_ids=["harness_retry#main/p9#1"]), "unknown_evidence_id"),
        (lambda d: d["must_read"][0]["entry"]["evidence"].update(evidence_ids=["dup_official#main/p2#1"]), "unknown_evidence_id"),
        (lambda d: d["must_read"][0].update(entry=entry("harness_retry", evidence=(0,))), "evidence_not_concrete"),
        (
            lambda d: d["must_read"][0].update(
                entry=entry("harness_retry", caveat={"text": "条件は確認できない。", "evidence_ids": ["dup_official#main/p1#1"]})
            ),
            "unknown_evidence_id",
        ),
        (lambda d: d["must_read"][0]["entry"].update(why_read="画期的な手法で、必見である。"), "forbidden_abstract_praise"),
        (lambda d: d["must_read"][0]["entry"].update(why_read="再試行の上限—その決め方がわかる。"), "forbidden_fullwidth_dash"),
    ],
)
def test_a_decision_that_breaks_a_rule_is_rejected_with_the_rule_named(mutate, issue) -> None:
    data = copy.deepcopy(decision())
    mutate(data)
    assert issue in issue_types(data)


def test_an_insufficient_or_supporting_article_cannot_be_adopted() -> None:
    data = decision()
    thin = next(e for e in data["excluded"] if e["article_id"] == "thin_post")
    data["excluded"].remove(thin)
    data["worth_knowing"].append({"article_id": "thin_post", "scores": SCORES, "decision_reason": "x", "entry": entry("thin_post", evidence=(0,))})
    assert "insufficient_research_included" in issue_types(data)

    data = decision(duplicate_groups=[])
    hn = next(e for e in data["excluded"] if e["article_id"] == "dup_hn")
    data["excluded"].remove(hn)
    data["worth_knowing"].append({"article_id": "dup_hn", "scores": SCORES, "decision_reason": "x", "entry": entry("dup_official")})
    assert "supporting_article_included" in issue_types(data)


def test_a_rejected_decision_is_retried_and_a_valid_one_accepted() -> None:
    broken = decision()
    broken["excluded"].pop()
    calls: list[LLMCallMetrics] = []
    result = selector(ScriptedModel(broken, decision()), record=calls.append).select(PACKETS)

    assert result.succeeded
    first, second = result.metrics.attempts
    assert first.error.kind is ErrorKind.VALIDATION and first.validation_issues[0].type == "missing_article"
    assert second.error is None


def test_retries_stop_at_the_cap_and_the_selector_fails() -> None:
    broken = decision()
    broken["must_read"][0]["entry"]["why_read"] = "画期的である。"
    over_cap = decision(must_read=[decision()["must_read"][0]] * 6)
    result = selector(ScriptedModel(broken, over_cap, broken, decision())).select(PACKETS)

    assert not result.succeeded and result.output is None
    assert result.error.kind is ErrorKind.VALIDATION
    assert len(result.metrics.attempts) == 3
    # The banned wording is not repeated in the metrics.
    assert "画期的" not in result.metrics.model_dump_json()


def test_the_order_of_each_tier_is_kept_as_the_reading_order() -> None:
    data = decision()
    data["must_read"] = [data["worth_knowing"][0], data["must_read"][0]]
    data["worth_knowing"] = []
    result = selector(ScriptedModel(data)).select(PACKETS)
    assert [a.article_id for a in result.output.must_read] == ["dup_official", "harness_retry"]


# -- what the model reads ---------------------------------------------------------------


def test_the_prompt_tells_the_model_not_to_decide_by_the_total_score() -> None:
    assert "6軸の合計点では決めない" in SYSTEM_PROMPT
    assert "採用ゼロでもよい" in SYSTEM_PROMPT
    assert "must_read は最大5件、worth_knowing は最大8件" in SYSTEM_PROMPT


def test_the_ordinary_input_is_packets_and_partial_packets_add_their_paragraphs() -> None:
    model = ScriptedModel(decision())
    selector(model).select(PACKETS)
    prompt = model.requests[0].prompt

    assert prompt.count("<<<UNTRUSTED_ARTICLE_BODY>>>") >= len(PACKETS)
    # A complete packet is quotes only; a partial one adds the paragraphs behind its claims.
    assert "[numbers_no_conditions#main/p2#1] The new agent resolves 85 percent" in prompt
    assert "[harness_retry#main/p1#1]" not in prompt
    assert model.requests[0].schema == SelectorOutput.model_json_schema()
