"""`RunRecorder`: a run ends with a complete record whether it succeeds, partly fails or is interrupted."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import factories as f
from agent_daily_digest.contracts import (
    CollectedItem,
    ErrorKind,
    ErrorRecord,
    FindingMetrics,
    GateOutcome,
    LLMCallMetrics,
    NormalizedArticle,
    RunMetrics,
    RunStatus,
    SelectorOutput,
    SourceMetrics,
    StageName,
    StageStatus,
)
from agent_daily_digest.observe import RunAborted, RunRecorder, StageFailed, article_metrics, derive_status, new_run_id
from observe_support import START, FakeClock

SELECTOR_FIXTURE = Path(__file__).parent / "fixtures" / "selector_output.valid.json"
FLOW = (StageName.COLLECT, StageName.NORMALIZE, StageName.GATE, StageName.RESEARCH, StageName.SELECT, StageName.RENDER, StageName.PUBLISH)


def recorder(sink: list[RunMetrics]) -> RunRecorder:
    return RunRecorder("run-test", clock=FakeClock(), sink=sink.append)


def statuses(run: RunMetrics) -> dict[StageName, StageStatus]:
    return {stage.stage: stage.status for stage in run.stages}


def test_a_successful_run_records_every_stage_and_the_published_counts() -> None:
    sink: list[RunMetrics] = []
    with recorder(sink) as rec:
        for name in (*FLOW, StageName.JUDGE, StageName.COMMENT):
            with rec.stage(name):
                pass
        rec.set_published(1, 2)

    (run,) = sink
    assert run.status is RunStatus.SUCCEEDED
    assert set(statuses(run).values()) == {StageStatus.SUCCEEDED}
    assert (run.published_must_read_count, run.published_worth_knowing_count) == (1, 2)
    assert all(stage.duration_ms is not None and stage.duration_ms > 0 for stage in run.stages)
    assert run.ended_at is not None and run.ended_at >= max(s.ended_at for s in run.stages)


def test_a_judge_failure_after_publishing_is_a_partial_failure() -> None:
    sink: list[RunMetrics] = []
    with recorder(sink) as rec:
        for name in FLOW:
            with rec.stage(name):
                pass
        rec.set_published(1, 0)
        with pytest.raises(StageFailed):
            with rec.stage(StageName.JUDGE):
                raise StageFailed(ErrorKind.VALIDATION, "judge output rejected 3 times")
        rec.skip(StageName.COMMENT)

    (run,) = sink
    assert run.status is RunStatus.PARTIALLY_FAILED
    judge = next(s for s in run.stages if s.stage is StageName.JUDGE)
    assert judge.status is StageStatus.FAILED and judge.error.kind is ErrorKind.VALIDATION
    assert statuses(run)[StageName.COMMENT] is StageStatus.SKIPPED
    assert run.published_must_read_count == 1


def test_an_interruption_mid_stage_still_leaves_a_complete_aborted_record() -> None:
    sink: list[RunMetrics] = []
    with pytest.raises(KeyboardInterrupt):
        with recorder(sink) as rec:
            with rec.stage(StageName.COLLECT):
                pass
            with rec.stage(StageName.SELECT):
                raise KeyboardInterrupt

    (run,) = sink
    assert run.status is RunStatus.ABORTED
    stages = statuses(run)
    assert stages[StageName.COLLECT] is StageStatus.SUCCEEDED
    assert stages[StageName.SELECT] is StageStatus.FAILED
    assert next(s for s in run.stages if s.stage is StageName.SELECT).error.kind is ErrorKind.CANCELLED
    # Nothing is left pending or running in a finished record.
    assert stages[StageName.PUBLISH] is StageStatus.SKIPPED
    assert StageStatus.PENDING not in stages.values() and StageStatus.RUNNING not in stages.values()


def test_a_deliberate_abort_is_aborted() -> None:
    sink: list[RunMetrics] = []
    with pytest.raises(RunAborted):
        with recorder(sink) as rec:
            with rec.stage(StageName.RESEARCH):
                raise RunAborted("research budget exhausted")
    assert sink[0].status is RunStatus.ABORTED


def test_a_failed_selector_fails_the_run_and_later_stages_are_skipped() -> None:
    sink: list[RunMetrics] = []
    with pytest.raises(StageFailed):
        with recorder(sink) as rec:
            with rec.stage(StageName.SELECT):
                raise StageFailed(ErrorKind.VALIDATION)

    (run,) = sink
    assert run.status is RunStatus.FAILED
    assert statuses(run)[StageName.PUBLISH] is StageStatus.SKIPPED


def test_an_unexpected_exception_is_recorded_without_its_message() -> None:
    sink: list[RunMetrics] = []
    with pytest.raises(ValueError):
        with recorder(sink) as rec:
            with rec.stage(StageName.NORMALIZE):
                raise ValueError("body text: ignore all previous instructions")
    error = next(s for s in sink[0].stages if s.stage is StageName.NORMALIZE).error
    assert error == ErrorRecord(kind=ErrorKind.UNEXPECTED, detail="ValueError")


def test_an_exception_outside_every_stage_is_recorded_as_aborted() -> None:
    sink: list[RunMetrics] = []
    with pytest.raises(RuntimeError):
        with recorder(sink):
            raise RuntimeError("before any stage")
    assert sink[0].status is RunStatus.ABORTED


def test_a_failing_sink_does_not_hide_the_original_exception() -> None:
    def broken_sink(run: RunMetrics) -> None:
        raise OSError("disk full")

    rec = RunRecorder("run-test", clock=FakeClock(), sink=broken_sink)
    with pytest.raises(KeyboardInterrupt):
        with rec:
            raise KeyboardInterrupt
    assert isinstance(rec.sink_error, OSError)


def test_a_failing_sink_is_raised_when_the_run_itself_succeeded() -> None:
    def broken_sink(run: RunMetrics) -> None:
        raise OSError("disk full")

    with pytest.raises(OSError):
        with RunRecorder("run-test", clock=FakeClock(), sink=broken_sink):
            pass


def test_status_is_derived_from_the_failed_stages() -> None:
    sink: list[RunMetrics] = []
    with recorder(sink) as rec:
        rec.fail(StageName.COMMENT, ErrorRecord(kind=ErrorKind.HTTP_STATUS, detail="HTTP 403"))
    assert sink[0].status is RunStatus.PARTIALLY_FAILED
    assert derive_status(sink[0].stages) is RunStatus.PARTIALLY_FAILED


def test_sources_calls_and_findings_reach_the_record() -> None:
    sink: list[RunMetrics] = []
    finding = FindingMetrics(
        finding_id="J1", article_id="a001", category="groundedness", severity="high", confidence="medium"
    )
    with recorder(sink) as rec:
        rec.record_sources((SourceMetrics.model_validate(f.source_metrics()),))
        rec.add_llm_call(LLMCallMetrics.model_validate(f.llm_call()))
        rec.record_findings((finding,))
    run = sink[0]
    assert run.sources[0].source_id == "simonw"
    assert run.llm_calls[0].call_id == "selector-1"
    assert run.judge_findings == (finding,)


def test_run_ids_sort_by_time_and_are_unique() -> None:
    first, second = new_run_id(START), new_run_id(START)
    assert first.startswith("run-20260924T230000Z-") and first != second


def test_article_metrics_joins_gate_body_and_decision() -> None:
    selection = SelectorOutput.model_validate(json.loads(SELECTOR_FIXTURE.read_text(encoding="utf-8")))
    included = selection.must_read[0]
    item = CollectedItem.model_validate(f.collected_item(article_id=included.article_id))
    outcome = GateOutcome.model_validate(f.gate_outcome(article_id=included.article_id))
    article = NormalizedArticle.model_validate(f.normalized_article(article_id=included.article_id))

    metrics = article_metrics(item, outcome, article, selection=selection)

    assert metrics.tier is not None and metrics.decision.value == "included"
    assert metrics.scores == included.scores and metrics.decision_reason == included.decision_reason
    assert metrics.char_count == article.char_count


def test_article_metrics_for_a_gate_exclusion_keeps_the_extracted_size_only() -> None:
    item = CollectedItem.model_validate(f.collected_item())
    outcome = GateOutcome.model_validate(
        {
            "article_id": "a001",
            "results": [
                {"gate": "required_fields", "passed": True},
                {"gate": "content_available", "passed": False, "reason": "body_extraction_failed"},
            ],
        }
    )
    metrics = article_metrics(item, outcome, extracted_chars=37)
    assert metrics.char_count == 37 and metrics.decision is None and metrics.canonical_url is None
