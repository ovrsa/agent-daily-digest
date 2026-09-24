"""Aggregation and presentation are separate: `summarize` computes, `render_summary` only formats."""

from __future__ import annotations

import inspect

import factories as f
from digest_contracts import ErrorKind, LLMRole, RunMetrics, RunStatus, Severity
from digest_observe import render_summary, summarize
from digest_observe import report as report_module


def sample_run() -> RunMetrics:
    failed_attempt = f.llm_attempt(
        status="failed",
        error={"kind": "validation", "detail": "2 validation issue(s)"},
        validation_issues=[{"loc": "must_read.0.scores", "type": "missing"}],
        usage={"input_tokens": 1000, "output_tokens": 100},
        cost={"usd": 0.01, "basis": "estimated"},
    )
    second = f.llm_attempt(attempt_number=2, started_at=f.T1, ended_at=f.T2)
    stages = [f.stage(stage=n) for n in f.ALL_STAGES]
    stages[-1] = f.stage(stage="comment", status="failed", error={"kind": "http_status", "detail": "HTTP 403"})
    return RunMetrics.model_validate(
        f.run_metrics(
            status="partially_failed",
            stages=stages,
            sources=[
                f.source_metrics(),
                f.source_metrics(source_id="reddit_claudeai", source_kind="discovery", status="failed", item_count=0,
                                 failure={"kind": "http_status", "detail": "HTTP 403"}),
            ],
            articles=[
                f.article_metrics(),
                f.article_metrics(article_id="a002", tier=None, decision="excluded", gate=f.gate_outcome(article_id="a002")),
                f.article_metrics(
                    article_id="a003", canonical_url=None, body_source=None, char_count=40, scores=None, decision=None,
                    tier=None, decision_reason=None,
                    gate={"article_id": "a003", "results": [
                        {"gate": "required_fields", "passed": True},
                        {"gate": "content_available", "passed": False, "reason": "body_extraction_failed"},
                    ]},
                ),
            ],
            llm_calls=[
                f.llm_call(attempts=[failed_attempt, second]),
                f.llm_call(call_id="judge-1", role="judge", prompt_version="judge-v1"),
            ],
            judge_findings=[
                {"finding_id": "J1", "article_id": "a001", "category": "groundedness", "severity": "high", "confidence": "medium"},
                {"finding_id": "J2", "article_id": "a002", "category": "exclusion_validity", "severity": "low", "confidence": "medium"},
            ],
        )
    )


def test_summarize_counts_every_part_of_the_run() -> None:
    summary = summarize(sample_run())

    assert summary.status is RunStatus.PARTIALLY_FAILED
    assert summary.sources_succeeded == 1
    assert summary.sources_failed == (("reddit_claudeai", ErrorKind.HTTP_STATUS),)
    assert summary.gate_passed == 2 and summary.gate_exclusions == (("body_extraction_failed", 1),)
    assert (summary.selected_must_read, summary.selected_worth_knowing, summary.selector_excluded) == (1, 0, 1)
    assert summary.published_must_read == 1

    selector, judge = summary.llm
    assert selector.role is LLMRole.SELECTOR
    assert (selector.calls, selector.attempts, selector.retries, selector.validation_failures) == (1, 2, 1, 1)
    assert selector.input_tokens == 1000 + 12000 and selector.cost_usd == 0.073
    assert judge.role is LLMRole.JUDGE and judge.prompt_versions == ("judge-v1",)
    assert summary.total_cost_usd == 0.073 + 0.063
    assert summary.findings_by_severity == ((Severity.HIGH, 1), (Severity.LOW, 1))


def test_render_summary_prints_figures_and_no_free_text() -> None:
    run = sample_run()
    text = render_summary(summarize(run))

    assert "partially_failed" in text and "reddit_claudeai: http_status" in text
    assert "| selector | claude-sonnet-5 | selector-v1 | 1 | 0 | 1 | 1 |" in text
    # Decision reasons and error details are free text; the summary does not carry them.
    assert run.articles[0].decision_reason not in text
    assert "HTTP 403" not in text


def test_the_presentation_layer_does_not_read_run_metrics() -> None:
    source = inspect.getsource(report_module)
    assert "RunMetrics" not in source and "digest_contracts" not in source.split("from .summary")[0]
