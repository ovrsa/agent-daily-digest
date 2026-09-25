"""The command line's wiring, and a dry run that leaves the repository as it was."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from digest_contracts import RunStatus
from digest_pipeline import DRY_RUN_COMMIT, DryRunPublisher, GitPublisher, RunResult, build, summary
from digest_pipeline.config import DEFAULT_MAX_ARTICLES, load_max_articles
from pipeline_support import DIGEST_DATE, ROOT, RoutedModel, decision, fetcher, finding, report

RUN_ID = "run-20260925T070000Z-abcdef"


def checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    shutil.copy2(ROOT / "config" / "config.json", repo / "config" / "config.json")
    shutil.copytree(ROOT / "digests", repo / "digests")
    return repo


def snapshot(directory: Path) -> dict[str, bytes]:
    return {str(path.relative_to(directory)): path.read_bytes() for path in sorted(directory.rglob("*")) if path.is_file()}


def test_a_dry_run_publishes_into_its_own_directory_and_runs_no_command(tmp_path: Path, monkeypatch) -> None:
    repo = checkout(tmp_path)
    before = snapshot(repo)

    def forbidden(*args, **kwargs):
        raise AssertionError("a dry run ran a command")

    monkeypatch.setattr(subprocess, "run", forbidden)
    model = RoutedModel(selector=[decision()], judge=[{"findings": [finding()]}])
    plan = build(repo, dry_run=True, run_id=RUN_ID, invoke=model, fetch=fetcher(), collect=lambda known: report())
    result = plan.pipeline.run(DIGEST_DATE, run_id=plan.run_id)

    assert result.status is RunStatus.SUCCEEDED and result.commit == DRY_RUN_COMMIT
    out = repo / "logs" / "dry-run" / RUN_ID
    assert plan.out_dir == out and isinstance(plan.pipeline.publisher, DryRunPublisher)
    assert result.digest_path == out / "digests" / "2026-09-25.md" and result.digest_path.exists()
    assert (out / "state" / "processed.json").exists()
    assert (out / "commit.txt").read_text(encoding="utf-8").startswith("digest: 2026-09-25\n")
    assert (out / "comment.md").read_text(encoding="utf-8").startswith(f"run: `{RUN_ID}`")
    assert len(list((out / "metrics").glob("*.json"))) == 1
    # Nothing outside the dry run's directory changed.
    after = {path: data for path, data in snapshot(repo).items() if not path.startswith("logs/dry-run/")}
    assert after == before


def test_a_dry_run_starts_from_the_committed_state(tmp_path: Path) -> None:
    repo = checkout(tmp_path)
    (repo / "state").mkdir()
    (repo / "state" / "processed.json").write_text('{"records": []}\n', encoding="utf-8")
    plan = build(repo, dry_run=True, run_id=RUN_ID, invoke=RoutedModel(), fetch=fetcher(), collect=lambda known: report(items=()))
    assert plan.pipeline.paths.state_path.read_text(encoding="utf-8") == '{"records": []}\n'
    assert plan.pipeline.paths.state_path != repo / "state" / "processed.json"


def test_a_real_run_works_in_the_repository_and_publishes_with_git(tmp_path: Path) -> None:
    repo = checkout(tmp_path)
    plan = build(repo, dry_run=False, run_id=RUN_ID, invoke=RoutedModel(), fetch=fetcher(), collect=lambda known: report())
    assert plan.out_dir is None and isinstance(plan.pipeline.publisher, GitPublisher)
    paths = plan.pipeline.paths
    assert (paths.digests_dir, paths.state_path, paths.judge_log_dir) == (
        repo / "digests",
        repo / "state" / "processed.json",
        repo / "logs" / "judge",
    )
    assert plan.pipeline.store.directory == repo / "logs" / "metrics"
    assert plan.pipeline.models.selector == "claude-sonnet-5"
    assert plan.pipeline.max_articles == load_max_articles(repo / "config" / "config.json")
    capped = build(repo, dry_run=True, run_id=RUN_ID, invoke=RoutedModel(), fetch=fetcher(), collect=lambda known: report(), max_articles=7)
    assert capped.pipeline.max_articles == 7


def test_the_article_cap_comes_from_the_config_and_a_broken_value_stops_the_run_before_it_starts(tmp_path: Path) -> None:
    import json

    import pytest

    repo = checkout(tmp_path)
    config = repo / "config" / "config.json"
    data = json.loads(config.read_text(encoding="utf-8"))
    # The shipped value and the fallback agree, so a config without the block runs the same.
    assert load_max_articles(config) == data["run"]["max_articles"] == DEFAULT_MAX_ARTICLES

    del data["run"]
    config.write_text(json.dumps(data), encoding="utf-8")
    assert load_max_articles(config) == DEFAULT_MAX_ARTICLES

    for bad in ({"max_articles": 0}, {"max_articles": -1}, {"max_articles": 2.5}, {"max_articles": True},
                {"max_articles": "20"}, {"max_articles": None}, {"max_article": 20}, [20]):
        data["run"] = bad
        config.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError):
            load_max_articles(config)
        with pytest.raises(ValueError):
            build(repo, dry_run=True, run_id=RUN_ID, invoke=RoutedModel(), fetch=fetcher(), collect=lambda known: report())


def test_the_article_cap_is_passed_through_and_must_be_positive(tmp_path: Path, monkeypatch, capsys) -> None:
    import pytest

    from digest_pipeline import cli

    seen = {}

    def fake_build(repo, **kw):
        seen.update(kw)
        return cli.Plan(_Stub(RunResult(RUN_ID, RunStatus.SUCCEEDED)), RUN_ID, None)

    monkeypatch.setattr(cli, "build", fake_build)
    assert cli.main(["--repo", str(tmp_path), "--dry-run", "--max-articles", "5"]) == 0
    assert seen == {"dry_run": True, "max_articles": 5}
    with pytest.raises(SystemExit):
        cli.main(["--repo", str(tmp_path), "--max-articles", "0"])
    assert "must be at least 1" in capsys.readouterr().err


def test_the_summary_names_the_run_its_status_and_where_to_look(tmp_path: Path) -> None:
    text = summary(RunResult(RUN_ID, RunStatus.PARTIALLY_FAILED, "abc", tmp_path / "d.md"), tmp_path)
    assert text.splitlines() == [f"run: {RUN_ID}", "status: partially_failed", "commit: abc", f"digest: {tmp_path / 'd.md'}", f"dry-run output: {tmp_path}"]
    capped = summary(RunResult(RUN_ID, RunStatus.SUCCEEDED, deferred=110), None)
    assert "deferred: 110 articles past the gates were not researched (the article cap)" in capped.splitlines()


def test_the_exit_code_is_zero_only_when_the_digest_run_finished_cleanly(tmp_path: Path, monkeypatch, capsys) -> None:
    from digest_contracts import ErrorKind, ErrorRecord
    from digest_pipeline import cli

    outcomes = {
        RunStatus.SUCCEEDED: 0,
        RunStatus.PARTIALLY_FAILED: 0,  # the digest is out; only the Judge or the comment failed
        RunStatus.FAILED: 1,
    }
    for status, expected in outcomes.items():
        result = RunResult(RUN_ID, status)
        monkeypatch.setattr(cli, "build", lambda repo, **kw: cli.Plan(_Stub(result), RUN_ID, None))
        assert cli.main(["--repo", str(tmp_path), "--date", "2026-09-25"]) == expected
    # A run that finished but could not write its metrics is reported as a failure.
    broken = RunResult(RUN_ID, RunStatus.SUCCEEDED, error=ErrorRecord(kind=ErrorKind.UNEXPECTED))
    monkeypatch.setattr(cli, "build", lambda repo, **kw: cli.Plan(_Stub(broken), RUN_ID, None))
    assert cli.main(["--repo", str(tmp_path)]) == 1
    failed_twice = RunResult(RUN_ID, RunStatus.PARTIALLY_FAILED, metrics_error=ErrorRecord(kind=ErrorKind.UNEXPECTED))
    monkeypatch.setattr(cli, "build", lambda repo, **kw: cli.Plan(_Stub(failed_twice), RUN_ID, None))
    assert cli.main(["--repo", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert f"run: {RUN_ID}" in out and "metrics error: unexpected" in out


class _Stub:
    def __init__(self, result: RunResult) -> None:
        self.result = result

    def run(self, digest_date, *, run_id):
        return self.result
