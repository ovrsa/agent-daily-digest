"""The daily run end to end, one test per branch of the Design Doc's Failure policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from digest_contracts import Decision, ErrorKind, RunStatus, StageName, StageStatus
from digest_observe import LLMInvocationError, MetricsStore
from digest_normalize import extract_document
from normalize_helpers import read_html
from pipeline_support import (
    DIGEST_DATE,
    ITEMS,
    RESULTS_URL,
    FakePublisher,
    RoutedModel,
    decision,
    finding,
    pipeline,
    report,
    state_of,
    workspace,
)

RUN_ID = "run-20260925T070000Z-abcdef"


def run(tmp_path: Path, model: RoutedModel, publisher: FakePublisher | None = None, **kwargs):
    publisher = publisher or FakePublisher()
    result = pipeline(tmp_path, model, publisher, **kwargs).run(DIGEST_DATE, run_id=RUN_ID)
    (metrics,) = MetricsStore(tmp_path / "metrics").load()
    return result, publisher, metrics


def stages(metrics) -> dict[str, str]:
    return {stage.stage.value: stage.status.value for stage in metrics.stages}


def readme(tmp_path: Path) -> bytes:
    return (workspace(tmp_path).digests_dir / "README.md").read_bytes()


def happy_model(**kwargs) -> RoutedModel:
    kwargs.setdefault("selector", [decision()])
    kwargs.setdefault("judge", [{"findings": [finding()]}])
    return RoutedModel(**kwargs)


# -- publish succeeds ------------------------------------------------------------------


def test_a_run_publishes_the_digest_and_posts_the_report_on_its_commit(tmp_path) -> None:
    result, publisher, metrics = run(tmp_path, happy_model())

    assert result.status is RunStatus.SUCCEEDED and result.published
    assert publisher.names() == ["sync", "publish", "comment"]
    (files, message), (commit, body) = publisher.calls[1][1], publisher.calls[2][1]
    digests = workspace(tmp_path).digests_dir
    assert [f.name for f in files] == ["2026-09-25.md", "README.md", "processed.json"]
    assert message == "digest: 2026-09-25"
    # The report goes on the digest commit and names the run, which also names the metrics file.
    assert commit == result.commit == publisher.commit
    assert body.startswith(f"run: `{RUN_ID}` / digest: 2026-09-25\n\n## Judge レポート")
    assert "### J1 [medium] groundedness" in body
    assert metrics.run_id == RUN_ID and all(status == "succeeded" for status in stages(metrics).values())
    assert (metrics.published_must_read_count, metrics.published_worth_knowing_count) == (1, 0)
    assert [f.finding_id for f in metrics.judge_findings] == ["J1"]
    digest = (digests / "2026-09-25.md").read_text(encoding="utf-8")
    assert digest.count("Harness retry budget") == 1
    # The Judge only comments: its suggested fix never reaches the published digest.
    assert "留保に、測定した実行の件数" not in digest and publisher.calls[1][1][0][0].read_text(encoding="utf-8") == digest
    assert "[2026-09-25](./2026-09-25.md)" in (digests / "README.md").read_text(encoding="utf-8")


def test_every_article_the_selector_decided_is_recorded_with_its_decision(tmp_path) -> None:
    run(tmp_path, happy_model())
    decisions = {record.canonical_url.rsplit("/", 1)[1]: record.last_decision for record in state_of(tmp_path).records}
    # a003 never passed the gates, so nothing about it is stored.
    assert decisions == {"retry-budget": Decision.INCLUDED, "eval-method": Decision.EXCLUDED}


def test_metrics_carry_every_article_the_calls_and_the_decisions(tmp_path) -> None:
    _result, _publisher, metrics = run(tmp_path, happy_model())
    by_id = {article.article_id: article for article in metrics.articles}
    assert set(by_id) == {"a001", "a002", "a003"}
    assert by_id["a001"].decision is Decision.INCLUDED and by_id["a002"].decision is Decision.EXCLUDED
    assert not by_id["a003"].gate.passed and by_id["a003"].decision is None
    assert [call.role.value for call in metrics.llm_calls] == ["research", "research", "selector", "judge"]


def test_research_gets_the_links_in_the_article_body(tmp_path) -> None:
    model = happy_model()
    run(tmp_path, model)
    a002 = next(r for r in model.requests["research"] if "article_id: a002" in r.prompt)
    assert f"- {RESULTS_URL}" in a002.prompt


def test_a_second_run_passes_the_known_urls_to_collection_and_drops_the_articles_at_the_gates(tmp_path) -> None:
    run(tmp_path, happy_model())
    seen: list[frozenset[str]] = []

    def collect(known):
        seen.append(known)
        return report()

    # The same workspace, so the second run reads the state the first one wrote.
    model = RoutedModel()
    second = pipeline(tmp_path, model, FakePublisher(), collect=collect).run(DIGEST_DATE, run_id="run-20260925T080000Z-abcdef")
    assert seen == [frozenset({"https://example.com/posts/retry-budget", "https://example.com/posts/eval-method"})]
    assert second.status is RunStatus.SUCCEEDED and not second.published
    assert model.requests["research"] == []


def test_article_bodies_are_kept_out_of_the_metrics(tmp_path) -> None:
    # A decision reason that copies the article is refused by the store, so no body reaches logs/.
    body = extract_document(read_html("article_basic")).body_text
    copied = max(body.split("\n\n"), key=len)[:120]
    leaky = decision()
    leaky["must_read"][0]["decision_reason"] = f"本文: {copied}"
    result = pipeline(tmp_path, happy_model(selector=[leaky]), FakePublisher()).run(DIGEST_DATE, run_id=RUN_ID)
    assert result.published and result.error is not None and result.error.kind is ErrorKind.UNEXPECTED
    assert MetricsStore(tmp_path / "metrics").load() == ()


def test_a_metrics_failure_on_top_of_a_run_failure_is_reported_too(tmp_path) -> None:
    body = extract_document(read_html("article_basic")).body_text
    leaky = decision()
    leaky["must_read"][0]["decision_reason"] = f"本文: {max(body.split(chr(10) * 2), key=len)[:120]}"
    result = pipeline(tmp_path, happy_model(selector=[leaky]), FakePublisher(fail_on="publish")).run(DIGEST_DATE, run_id=RUN_ID)
    assert result.status is RunStatus.FAILED and result.error.kind is ErrorKind.NETWORK
    assert result.metrics_error is not None and result.metrics_error.kind is ErrorKind.UNEXPECTED


# -- nothing to publish ------------------------------------------------------------------


def test_nothing_collected_ends_normally_without_publishing(tmp_path) -> None:
    model = RoutedModel()
    result, publisher, metrics = run(tmp_path, model, collect=lambda known: report(items=()))
    assert result.status is RunStatus.SUCCEEDED and not result.published and result.commit is None
    assert publisher.names() == ["sync"]
    assert stages(metrics)["collect"] == "succeeded" and stages(metrics)["normalize"] == "skipped"
    assert model.requests == {"research": [], "selector": [], "judge": []}


def test_nothing_past_the_gates_ends_normally_without_calling_the_model(tmp_path) -> None:
    model = RoutedModel()
    result, publisher, metrics = run(tmp_path, model, collect=lambda known: report(items=[ITEMS[2]]))
    assert result.status is RunStatus.SUCCEEDED and not result.published
    assert publisher.names() == ["sync"] and model.requests["research"] == []
    assert stages(metrics)["gate"] == "succeeded" and stages(metrics)["research"] == "skipped"
    assert not (tmp_path / "state" / "processed.json").exists()


def test_adopting_nothing_commits_the_state_alone_and_skips_the_audit(tmp_path) -> None:
    workspace(tmp_path)
    before = readme(tmp_path)
    model = RoutedModel(selector=[decision(adopt=False)])
    result, publisher, metrics = run(tmp_path, model)

    assert result.status is RunStatus.SUCCEEDED and not result.published and result.digest_path is None
    assert publisher.names() == ["sync", "publish"]
    (files, message) = publisher.calls[1][1]
    assert [f.name for f in files] == ["processed.json"] and message == "state: 2026-09-25"
    assert result.commit == publisher.commit
    assert not (workspace(tmp_path).digests_dir / "2026-09-25.md").exists()
    assert readme(tmp_path) == before
    assert {r.last_decision for r in state_of(tmp_path).records} == {Decision.EXCLUDED}
    assert model.requests["judge"] == [] and stages(metrics)["judge"] == "skipped"
    assert (metrics.published_must_read_count, metrics.published_worth_knowing_count) == (0, 0)


# -- failures that keep the digest unpublished -----------------------------------------------


def test_a_collection_failure_publishes_nothing(tmp_path) -> None:
    def collect(known):
        raise OSError("disk")

    result, publisher, metrics = run(tmp_path, RoutedModel(), collect=collect)
    assert result.status is RunStatus.FAILED and not result.published
    assert publisher.names() == ["sync"] and stages(metrics)["collect"] == "failed"
    assert result.error.kind is ErrorKind.UNEXPECTED


def test_every_source_failing_is_a_collection_failure(tmp_path) -> None:
    result, publisher, metrics = run(tmp_path, RoutedModel(), collect=lambda known: report(failed=True))
    assert result.status is RunStatus.FAILED and publisher.names() == ["sync"]
    collect = next(stage for stage in metrics.stages if stage.stage is StageName.COLLECT)
    assert collect.status is StageStatus.FAILED and collect.error.kind is ErrorKind.NETWORK
    assert collect.error.detail == "all 1 sources failed"


def test_a_checkout_that_cannot_be_synced_publishes_nothing(tmp_path) -> None:
    result, publisher, metrics = run(tmp_path, RoutedModel(), FakePublisher(fail_on="sync"))
    assert result.status is RunStatus.FAILED and publisher.names() == ["sync"]
    assert stages(metrics)["collect"] == "failed"


def test_a_normalization_failure_publishes_nothing(tmp_path) -> None:
    def broken_fetch(url):
        raise RuntimeError("parser crashed")

    result, publisher, metrics = run(tmp_path, RoutedModel(), fetch=broken_fetch)
    assert result.status is RunStatus.FAILED and publisher.names() == ["sync"]
    assert stages(metrics)["normalize"] == "failed" and stages(metrics)["select"] == "skipped"


def test_a_research_failure_publishes_nothing(tmp_path) -> None:
    model = RoutedModel(research={"a001": [RuntimeError("bug in research")]})
    result, publisher, metrics = run(tmp_path, model)
    assert result.status is RunStatus.FAILED and publisher.names() == ["sync"]
    assert stages(metrics)["research"] == "failed"


def test_a_selector_that_keeps_failing_validation_publishes_nothing_after_its_retries(tmp_path) -> None:
    invalid = decision()
    invalid["excluded"].pop()  # a002 is decided nowhere
    model = RoutedModel(selector=[invalid, invalid, invalid])
    result, publisher, metrics = run(tmp_path, model)

    assert result.status is RunStatus.FAILED and not result.published
    assert publisher.names() == ["sync"] and len(model.requests["selector"]) == 3
    select = next(stage for stage in metrics.stages if stage.stage is StageName.SELECT)
    assert select.status is StageStatus.FAILED and select.error.kind is ErrorKind.VALIDATION
    assert not (tmp_path / "state" / "processed.json").exists()  # the articles are tried again next run
    assert model.requests["judge"] == []


def test_a_selector_whose_model_call_fails_publishes_nothing(tmp_path) -> None:
    model = RoutedModel(selector=[LLMInvocationError(ErrorKind.AUTHENTICATION)])
    result, publisher, metrics = run(tmp_path, model)
    assert result.status is RunStatus.FAILED and publisher.names() == ["sync"]
    select = next(stage for stage in metrics.stages if stage.stage is StageName.SELECT)
    assert select.error.kind is ErrorKind.AUTHENTICATION


def test_a_render_failure_publishes_nothing(tmp_path, monkeypatch) -> None:
    # The Selector already rejects what the renderer bans, so this guard is a second line;
    # force it to prove the stage stops the run on its own.
    from digest_pipeline import pipeline as module
    from digest_render import ForbiddenArtifactError

    def refuse(*args, **kwargs):
        raise ForbiddenArtifactError(())

    monkeypatch.setattr(module, "render_digest", refuse)
    result, publisher, metrics = run(tmp_path, happy_model())
    assert result.status is RunStatus.FAILED and not result.published
    assert publisher.names() == ["sync"] and stages(metrics)["render"] == "failed"
    assert not (tmp_path / "state" / "processed.json").exists()


def test_a_failed_publish_puts_the_files_back_and_skips_the_audit(tmp_path) -> None:
    workspace(tmp_path)
    before = readme(tmp_path)
    model = happy_model()
    result, publisher, metrics = run(tmp_path, model, FakePublisher(fail_on="publish"))

    assert result.status is RunStatus.FAILED and not result.published and result.commit is None
    assert publisher.names() == ["sync", "publish"]
    assert stages(metrics)["publish"] == "failed" and stages(metrics)["judge"] == "skipped"
    assert not (workspace(tmp_path).digests_dir / "2026-09-25.md").exists()
    assert readme(tmp_path) == before
    assert not (tmp_path / "state" / "processed.json").exists()
    assert model.requests["judge"] == []
    assert (metrics.published_must_read_count, metrics.published_worth_knowing_count) == (0, 0)


def test_an_interrupt_during_publish_puts_the_files_back_and_is_raised(tmp_path) -> None:
    workspace(tmp_path)
    before = readme(tmp_path)
    publisher = FakePublisher(fail_on="publish", failure=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        pipeline(tmp_path, happy_model(), publisher).run(DIGEST_DATE, run_id=RUN_ID)
    assert readme(tmp_path) == before and not (tmp_path / "state" / "processed.json").exists()
    (metrics,) = MetricsStore(tmp_path / "metrics").load()
    assert metrics.status is RunStatus.ABORTED


# -- failures after publishing -----------------------------------------------------------------


def test_a_judge_that_fails_leaves_the_digest_published_and_says_so_on_the_commit(tmp_path) -> None:
    bad = {"findings": [finding() | {"article_id": "a003"}]}  # a003 is not an audit target
    result, publisher, metrics = run(tmp_path, happy_model(judge=[bad, bad]))

    assert result.status is RunStatus.PARTIALLY_FAILED and result.published
    assert publisher.names() == ["sync", "publish", "comment"]
    judge = next(stage for stage in metrics.stages if stage.stage is StageName.JUDGE)
    assert judge.status is StageStatus.FAILED and judge.error.kind is ErrorKind.VALIDATION
    assert "- 結果: 失敗（validation）" in publisher.calls[2][1][1]
    assert metrics.judge_findings == ()


def test_a_judge_that_raises_is_contained_and_the_digest_stays_published(tmp_path) -> None:
    result, publisher, metrics = run(tmp_path, happy_model(judge=[RuntimeError("bug in the judge")]))
    assert result.status is RunStatus.PARTIALLY_FAILED and result.published
    assert stages(metrics)["judge"] == "failed" and stages(metrics)["comment"] == "succeeded"
    assert "- 結果: 失敗（unexpected）" in publisher.calls[2][1][1]


def test_a_comment_that_cannot_be_posted_is_kept_in_the_local_log(tmp_path) -> None:
    result, publisher, metrics = run(tmp_path, happy_model(), FakePublisher(fail_on="comment"))

    assert result.status is RunStatus.PARTIALLY_FAILED and result.published
    assert stages(metrics)["comment"] == "failed"
    kept = (tmp_path / "logs" / "judge" / f"{RUN_ID}.md").read_text(encoding="utf-8")
    assert kept.startswith(f"commit: {publisher.commit}\n\nrun: `{RUN_ID}`")
    assert "### J1 [medium] groundedness" in kept


def test_the_comment_carries_no_long_body_text_and_no_secret(tmp_path) -> None:
    token = "ghp_" + "A" * 36
    leaky = finding(problem=f"トークン {token} が本文にある。" + "長い説明。" * 200)
    result, publisher, _metrics = run(tmp_path, happy_model(judge=[{"findings": [leaky]}]))
    body = publisher.calls[2][1][1]
    assert token not in body and "&lt;redacted>" in body
    assert max(len(line) for line in body.splitlines()) < 500
    # No paragraph of the article is reposted; only the short quote the Judge chose.
    article_body = extract_document(read_html("article_basic")).body_text
    for paragraph in article_body.split("\n\n"):
        if len(paragraph) > 60:
            assert paragraph not in body
