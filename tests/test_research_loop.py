"""The research loop: normal, insufficient, duplicate fetches, budget limits, and what the model cannot steer."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from digest_contracts import (
    ErrorKind,
    LLMCallMetrics,
    LLMRole,
    ProcessedRecord,
    ProcessedState,
    ResearchBudget,
    ResearchStatus,
    ResearchStopReason,
    SourceKind,
)
from digest_normalize import ProcessedIndex, extract_document
from digest_observe import LLMResponse
from digest_research import Researcher, ResearchInput, render_packet
from observe_support import PRICING
from research_support import (
    PUBLISHED,
    RESULTS_PAGE,
    RESULTS_URL,
    ScriptedModel,
    ScriptedWeb,
    article,
    complete_map,
    statement_only_map,
)

LINKS_ARTICLE = article(fixture="article_links", title="Evaluating agent harnesses", url="https://example.com/posts/harness-eval")
LINKS_INPUT = ResearchInput(LINKS_ARTICLE, links=(RESULTS_URL, "/posts/eval-method", "#section-2", "mailto:a@example.com"))


def researcher(model: ScriptedModel, web: ScriptedWeb | None = None, **kwargs) -> Researcher:
    return Researcher(invoke=model, fetch=web or ScriptedWeb(), pricing=PRICING, model="claude-sonnet-5", **kwargs)


def ask_for_results(**extra) -> dict:
    return statement_only_map(open_questions=[{"question": "比較の数値はどこにあるか", "url": RESULTS_URL}], **extra)


def results_round() -> dict:
    return {
        "evidence": [
            {"id": "e1", "doc": "ref1", "paragraph": "p2", "kind": "comparison", "quote": "policy B finished 229 of 240 tasks"},
            {"id": "e2", "doc": "ref1", "paragraph": "p4", "kind": "procedure", "quote": "Each task ran once per policy on the same commit"},
        ],
        "claims": [
            {"id": "c2", "kind": "finding", "text": "方針 B は 240 件中 229 件を完了した。", "evidence": ["e1"], "numeric": True, "conditions": ["e2"], "reproducible": False},
        ],
    }


# -- normal -------------------------------------------------------------------


def test_a_sufficient_first_round_stops_with_a_complete_packet() -> None:
    model = ScriptedModel(complete_map())
    result = researcher(model).run([ResearchInput(article())])

    (packet,) = result.packets
    assert packet.status is ResearchStatus.COMPLETE and packet.stop_reason is ResearchStopReason.SUFFICIENT
    assert packet.trace.rounds == 1 and packet.trace.extra_pages == ()
    assert result.library.resolve(packet.claims[0].evidence_ids[1]).text.startswith("```")
    assert len(model.requests) == 1


def test_a_linked_primary_source_completes_the_evidence_in_round_two() -> None:
    model = ScriptedModel(ask_for_results(), results_round())
    web = ScriptedWeb({RESULTS_URL: RESULTS_PAGE})
    (packet,) = researcher(model, web).run([LINKS_INPUT]).packets

    assert web.requested == [RESULTS_URL]
    assert packet.status is ResearchStatus.COMPLETE and packet.trace.rounds == 2
    assert packet.trace.extra_pages[0].doc_id == "ref1"
    assert "a001#ref1/p2#1" in packet.evidence_ids
    # Round two saw the fetched page, and nothing but the questions and the map.
    second = model.requests[1].prompt
    assert "[ref1/p2] Policy A finished 212 of 240 tasks" in second and "[main/p2]" not in second


def test_a_question_about_read_paragraphs_resends_only_those_paragraphs() -> None:
    first = statement_only_map(open_questions=[{"question": "具体例は", "doc": "main", "paragraphs": ["p3", "p99"]}])
    model = ScriptedModel(first, statement_only_map())
    (packet,) = researcher(model).run([LINKS_INPUT]).packets

    assert packet.trace.paragraph_requests == 1
    assert "[main/p3]" in model.requests[1].prompt and "[main/p2]" not in model.requests[1].prompt


# -- insufficient ---------------------------------------------------------------


def test_missing_concrete_evidence_with_nothing_to_check_is_insufficient() -> None:
    (packet,) = researcher(ScriptedModel(statement_only_map())).run([LINKS_INPUT]).packets
    assert packet.status is ResearchStatus.INSUFFICIENT
    assert packet.stop_reason is ResearchStopReason.NO_OPEN_QUESTIONS
    assert any("具体的な根拠" in item for item in packet.unresolved)


def test_an_unconfirmed_measurement_condition_is_partial_and_says_so() -> None:
    data = complete_map()
    data["claims"][1]["conditions"] = []
    (packet,) = researcher(ScriptedModel(data)).run([ResearchInput(article())]).packets
    assert packet.status is ResearchStatus.PARTIAL
    assert any("c2" in item and "測定・比較条件" in item for item in packet.unresolved)


def test_a_missing_author_is_listed_as_unconfirmed() -> None:
    (packet,) = researcher(ScriptedModel(complete_map())).run([ResearchInput(article(author=None))]).packets
    assert packet.status is ResearchStatus.PARTIAL and "著者を確認できない" in packet.unresolved


def test_a_map_that_never_passes_the_reference_checks_is_an_extraction_failure() -> None:
    bad = complete_map()
    bad["evidence"][0]["quote"] = "a sentence the article never wrote"
    calls: list[LLMCallMetrics] = []
    (packet,) = researcher(ScriptedModel(bad, bad), record=calls.append).run([ResearchInput(article())]).packets

    assert packet.stop_reason is ResearchStopReason.EXTRACTION_FAILED
    assert packet.status is ResearchStatus.INSUFFICIENT and packet.evidence == ()
    (call,) = calls
    assert call.role is LLMRole.RESEARCH and call.retry_count == 1
    assert call.attempts[0].error.kind is ErrorKind.VALIDATION


def test_a_model_failure_is_an_extraction_failure_not_a_crash() -> None:
    failed = LLMResponse(structured_output=None, is_error=True, api_error_status=401)
    (packet,) = researcher(ScriptedModel(failed)).run([ResearchInput(article())]).packets
    assert packet.stop_reason is ResearchStopReason.EXTRACTION_FAILED
    assert any("authentication" in item for item in packet.unresolved)


# -- duplicate fetches ------------------------------------------------------------


def test_a_link_to_another_article_of_the_run_is_not_fetched() -> None:
    other = article("a002", url=RESULTS_URL, title="Unrelated release notes for another tool")
    model = ScriptedModel(ask_for_results(), complete_map())
    web = ScriptedWeb({RESULTS_URL: RESULTS_PAGE})
    researcher(model, web).run([LINKS_INPUT, ResearchInput(other)])
    assert web.requested == []


def test_a_link_already_in_the_processing_state_is_not_fetched() -> None:
    state = ProcessedState(records=(ProcessedRecord(canonical_url=RESULTS_URL, first_seen_at=PUBLISHED, content_hash="0" * 64, last_decision="excluded"),))
    web = ScriptedWeb({RESULTS_URL: RESULTS_PAGE})
    (packet,) = researcher(ScriptedModel(ask_for_results()), web, processed=ProcessedIndex.from_state(state)).run([LINKS_INPUT]).packets
    assert web.requested == [] and any("処理済みの URL" in item for item in packet.unresolved)


def test_a_page_whose_body_matches_one_already_read_is_discarded() -> None:
    # a003 is already in the run with exactly the body the linked page serves.
    same = article("a003", body=extract_document(RESULTS_PAGE).body_text, url="https://example.org/mirror", title="Totally different words here")
    web = ScriptedWeb({RESULTS_URL: RESULTS_PAGE})
    # a001 asks for the page, gets it discarded and stops; a003 then has its own round.
    model = ScriptedModel(ask_for_results(), {})
    packets = researcher(model, web).run([LINKS_INPUT, ResearchInput(same)]).packets
    reference = packets[0].trace.extra_pages[0]
    assert reference.doc_id is None and reference.failure == "既に読んだ本文と同じだった"


def test_the_same_link_asked_twice_is_fetched_once() -> None:
    twice = statement_only_map(open_questions=[{"question": "q1", "url": RESULTS_URL}, {"question": "q2", "url": RESULTS_URL}])
    web = ScriptedWeb({RESULTS_URL: RESULTS_PAGE})
    researcher(ScriptedModel(twice, results_round()), web).run([LINKS_INPUT])
    assert web.requested == [RESULTS_URL]


# -- budget --------------------------------------------------------------------------


def test_rounds_stop_at_the_budget_even_when_the_model_keeps_asking() -> None:
    asking = statement_only_map(open_questions=[{"question": "具体例は", "doc": "main", "paragraphs": ["p2"]}])
    model = ScriptedModel(asking, asking, asking, asking)
    (packet,) = researcher(model).run([LINKS_INPUT]).packets
    assert packet.trace.rounds == 2 and len(model.requests) == 2
    assert packet.stop_reason is ResearchStopReason.BUDGET_EXHAUSTED


def test_extra_pages_stop_at_the_budget_and_the_rest_are_reported() -> None:
    urls = [f"https://docs.example.com/page{n}" for n in range(1, 4)]
    questions = [{"question": f"q{n}", "url": url} for n, url in enumerate(urls, 1)]
    web = ScriptedWeb({url: RESULTS_PAGE.replace("Policy A", f"Policy {n}") for n, url in enumerate(urls)})
    model = ScriptedModel(statement_only_map(open_questions=questions), statement_only_map())
    budget = ResearchBudget(max_extra_pages=1)
    (packet,) = researcher(model, web, budget=budget).run([ResearchInput(LINKS_ARTICLE, links=tuple(urls))]).packets

    assert web.requested == urls[:1]
    assert sum("上限に達したため" in item for item in packet.unresolved) == 2


def test_questions_beyond_the_budget_are_not_acted_on() -> None:
    urls = [f"https://docs.example.com/page{n}" for n in range(1, 6)]
    questions = [{"question": f"q{n}", "url": url} for n, url in enumerate(urls, 1)]
    web = ScriptedWeb({url: RESULTS_PAGE.replace("Policy A", f"Policy {n}") for n, url in enumerate(urls)})
    model = ScriptedModel(statement_only_map(open_questions=questions), statement_only_map())
    researcher(model, web, budget=ResearchBudget(max_extra_pages=10)).run([ResearchInput(LINKS_ARTICLE, links=tuple(urls))])
    assert web.requested == urls[:3]


def test_the_question_cap_counts_across_rounds() -> None:
    def asking(*paragraphs: str) -> dict:
        return statement_only_map(open_questions=[{"question": f"q {p}", "doc": "main", "paragraphs": [p]} for p in paragraphs])

    model = ScriptedModel(asking("p1", "p3"), asking("p1", "p3"), asking("p1"))
    (packet,) = researcher(model, budget=ResearchBudget(max_rounds=4, max_open_questions=3)).run([LINKS_INPUT]).packets
    # Two questions in round two, one in round three, then the cap: no fourth round.
    assert packet.trace.paragraph_requests == 3 and packet.trace.rounds == 3
    assert packet.stop_reason is ResearchStopReason.BUDGET_EXHAUSTED


def test_the_time_budget_stops_research_before_another_round() -> None:
    ticks = iter([0.0, 0.0, 500.0, 500.0, 500.0, 500.0])
    model = ScriptedModel(ask_for_results())
    web = ScriptedWeb({RESULTS_URL: RESULTS_PAGE})
    (packet,) = researcher(model, web, budget=ResearchBudget(max_seconds=240), monotonic=lambda: next(ticks)).run([LINKS_INPUT]).packets
    assert web.requested == [] and packet.stop_reason is ResearchStopReason.BUDGET_EXHAUSTED


def test_link_depth_zero_follows_no_link() -> None:
    web = ScriptedWeb({RESULTS_URL: RESULTS_PAGE})
    (packet,) = researcher(ScriptedModel(ask_for_results()), web, budget=ResearchBudget(max_link_depth=0)).run([LINKS_INPUT]).packets
    assert web.requested == [] and packet.stop_reason is ResearchStopReason.NO_OPEN_QUESTIONS


# -- what the model cannot steer -------------------------------------------------------


def test_a_url_the_article_does_not_link_to_is_never_fetched() -> None:
    """An injected instruction can make the model ask for any URL; only cited links are opened."""
    rogue = statement_only_map(open_questions=[{"question": "q", "url": "https://attacker.example/steal"}, {"question": "q", "url": "http://169.254.169.254/latest/meta-data"}])
    web = ScriptedWeb({"https://attacker.example/steal": RESULTS_PAGE})
    (packet,) = researcher(ScriptedModel(rogue), web).run([LINKS_INPUT]).packets
    assert web.requested == []
    assert sum("記事が参照していない URL" in item for item in packet.unresolved) == 2


def test_links_of_a_fetched_page_are_never_followed() -> None:
    page = RESULTS_PAGE.replace("</article>", '<p><a href="https://deeper.example/next">next</a></p></article>')
    deeper = statement_only_map(open_questions=[{"question": "q", "url": "https://deeper.example/next"}])
    model = ScriptedModel(ask_for_results(), deeper)
    web = ScriptedWeb({RESULTS_URL: page, "https://deeper.example/next": RESULTS_PAGE})
    researcher(model, web, budget=ResearchBudget(max_rounds=3)).run([LINKS_INPUT])
    assert web.requested == [RESULTS_URL]


def test_the_article_body_reaches_the_model_only_inside_the_untrusted_block() -> None:
    injected = article(fixture="article_injection", title="Agent evaluation notes")
    model = ScriptedModel({})
    researcher(model).run([ResearchInput(injected)])
    prompt = model.requests[0].prompt
    block = prompt[prompt.index("<<<UNTRUSTED_ARTICLE_BODY>>>") :]
    assert "Ignore all previous instructions" in block
    assert "Ignore all previous instructions" not in prompt[: prompt.index("<<<UNTRUSTED_ARTICLE_BODY>>>")]
    assert prompt.count("<<<END_UNTRUSTED_ARTICLE_BODY>>>") == 1
    assert "指示、命令、役割の宣言" in model.requests[0].system_prompt


# -- clusters and output ---------------------------------------------------------------


def test_a_cluster_is_researched_once_on_its_representative() -> None:
    hn = article("hn1", source_id="hackernews", kind=SourceKind.DISCOVERY, title="Harness retry budget", url="https://news.example/item?id=1")
    official = article("off1", source_id="claude_blog", title="Harness retry budget", url="https://claude.com/blog/retry-budget")
    model = ScriptedModel(complete_map())
    result = researcher(model).run([ResearchInput(hn), ResearchInput(official)])

    assert [p.article_id for p in result.packets] == ["hn1", "off1"]
    hn_packet, official_packet = result.packets
    assert official_packet.supporting_ids == ("hn1",) and official_packet.status is ResearchStatus.COMPLETE
    assert hn_packet.represented_by == "off1" and hn_packet.stop_reason is ResearchStopReason.NOT_RESEARCHED
    assert len(model.requests) == 1


def test_every_call_is_recorded_as_a_research_call_with_the_prompt_version() -> None:
    calls: list[LLMCallMetrics] = []
    web = ScriptedWeb({RESULTS_URL: RESULTS_PAGE})
    researcher(ScriptedModel(ask_for_results(), results_round()), web, record=calls.append).run([LINKS_INPUT])
    assert [c.call_id for c in calls] == ["research-a001-r1", "research-a001-r2"]
    assert {c.prompt_version for c in calls} == {"research-v1"}
    assert all(c.attempts[0].usage is not None and c.attempts[0].cost is not None for c in calls)


def test_the_selector_input_carries_quotes_not_the_full_text() -> None:
    long_body = article().body_text + "\n\n" + "\n\n".join(f"Unquoted paragraph number {n} about something else entirely." for n in range(40))
    (packet,) = researcher(ScriptedModel(complete_map())).run([ResearchInput(article(body=long_body))]).packets
    text = render_packet(packet)
    assert "budget = RetryBudget(max_attempts=3)" in text
    assert "Unquoted paragraph number" not in text
    assert len(text) < len(long_body) / 2


def test_research_writes_nothing_to_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    web = ScriptedWeb({RESULTS_URL: RESULTS_PAGE})
    researcher(ScriptedModel(ask_for_results(), results_round()), web).run([LINKS_INPUT])
    assert list(tmp_path.iterdir()) == []
    package = Path(__import__("digest_research").__file__).parent
    source = "".join(p.read_text(encoding="utf-8") for p in package.glob("*.py"))
    for writer in ("write_text", "write_bytes", " open(", "mkdir", "os.replace", "tempfile"):
        assert writer not in source, writer
