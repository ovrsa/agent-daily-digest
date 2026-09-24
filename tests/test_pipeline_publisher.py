"""`GitPublisher` against real git repositories in a temporary directory, and `DryRunPublisher`.

The remote is a bare repository next to the clone, so nothing leaves the
machine. `gh` is never run: the comment test records the command instead.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from digest_contracts import ErrorKind
from digest_observe import StageFailed
from digest_pipeline import DRY_RUN_COMMIT, CommandFailed, DryRunPublisher, GitPublisher, run_command


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A clone of a bare `origin` with one commit on `main`."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--quiet", "--bare", "--initial-branch=main", str(origin)], check=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(clone)], check=True, capture_output=True)
    git(clone, "config", "user.name", "Digest Test")
    git(clone, "config", "user.email", "digest@example.com")
    git(clone, "checkout", "--quiet", "-b", "main")
    (clone / "digests").mkdir()
    (clone / "digests" / "README.md").write_text("index\n", encoding="utf-8")
    git(clone, "add", ".")
    git(clone, "commit", "--quiet", "-m", "init")
    git(clone, "push", "--quiet", "origin", "main")
    return clone


def remote_head(repo: Path) -> str:
    return git(repo.parent / "origin.git", "rev-parse", "main")


def test_publish_commits_only_the_given_paths_and_pushes_them(repo: Path) -> None:
    (repo / "digests" / "2026-09-25.md").write_text("digest\n", encoding="utf-8")
    (repo / "digests" / "README.md").write_text("index\n- 2026-09-25\n", encoding="utf-8")
    (repo / "notes.txt").write_text("the operator's own work\n", encoding="utf-8")
    git(repo, "add", "notes.txt")  # staged by someone else; must not ride along

    commit = GitPublisher(repo).publish([repo / "digests" / "2026-09-25.md", repo / "digests" / "README.md"], "digest: 2026-09-25")

    assert commit == git(repo, "rev-parse", "HEAD") == remote_head(repo)
    assert git(repo, "log", "-1", "--format=%s") == "digest: 2026-09-25"
    assert git(repo, "show", "--name-only", "--format=", "HEAD").splitlines() == ["digests/2026-09-25.md", "digests/README.md"]
    assert git(repo, "diff", "--cached", "--name-only") == "notes.txt"


def test_a_rejected_push_undoes_the_commit_and_unstages_the_paths(repo: Path) -> None:
    hook = repo.parent / "origin.git" / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'remote: rejected by policy' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    before = git(repo, "rev-parse", "HEAD")
    digest = repo / "digests" / "2026-09-25.md"
    digest.write_text("digest\n", encoding="utf-8")

    with pytest.raises(CommandFailed) as raised:
        GitPublisher(repo).publish([digest], "digest: 2026-09-25")

    assert git(repo, "rev-parse", "HEAD") == before == remote_head(repo)
    assert git(repo, "diff", "--cached", "--name-only") == ""
    assert raised.value.command[:2] == ("git", "push") and raised.value.kind is ErrorKind.UNEXPECTED
    assert raised.value.detail.startswith("git push exited 1")


def interrupted_at(step: str):
    """A runner that raises `KeyboardInterrupt` once `git <step>` has run (or, for push, instead of it)."""

    def run(args, cwd, stdin):
        if list(args[:2]) == ["git", step]:
            if step == "push":
                raise KeyboardInterrupt
            run_command(args, cwd, stdin)
            raise KeyboardInterrupt
        return run_command(args, cwd, stdin)

    return run


@pytest.mark.parametrize("step", ["push", "commit"])
def test_an_interrupt_before_the_push_completes_leaves_no_commit_behind(repo: Path, step: str) -> None:
    before = git(repo, "rev-parse", "HEAD")
    digest = repo / "digests" / "2026-09-25.md"
    digest.write_text("digest of a failed run\n", encoding="utf-8")

    with pytest.raises(KeyboardInterrupt):
        GitPublisher(repo, run=interrupted_at(step)).publish([digest], "digest: 2026-09-25")

    assert git(repo, "rev-parse", "HEAD") == before == remote_head(repo)
    assert git(repo, "diff", "--cached", "--name-only") == ""
    # The caller puts the files back; the next run's push must not carry the failed run's content.
    digest.unlink()
    nextday = repo / "digests" / "2026-09-26.md"
    nextday.write_text("digest\n", encoding="utf-8")
    GitPublisher(repo).publish([nextday], "digest: 2026-09-26")
    assert git(repo, "log", "--format=%s", "origin/main").splitlines()[:2] == ["digest: 2026-09-26", "init"]
    assert subprocess.run(
        ["git", "cat-file", "-e", "main:digests/2026-09-25.md"], cwd=repo.parent / "origin.git", capture_output=True
    ).returncode != 0


def test_a_path_outside_the_repository_is_refused_before_any_command(repo: Path, tmp_path: Path) -> None:
    ran: list[list[str]] = []

    def record(args, cwd, stdin):
        ran.append(list(args))
        return run_command(args, cwd, stdin)

    with pytest.raises(StageFailed, match="outside the repository"):
        GitPublisher(repo, run=record).publish([tmp_path / "elsewhere.md"], "digest: 2026-09-25")
    assert ran == []


def test_sync_fast_forwards_to_the_remote(repo: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "--quiet", str(repo.parent / "origin.git"), str(other)], check=True, capture_output=True)
    git(other, "config", "user.name", "Other")
    git(other, "config", "user.email", "other@example.com")
    (other / "merged.txt").write_text("a merged pull request\n", encoding="utf-8")
    git(other, "add", "merged.txt")
    git(other, "commit", "--quiet", "-m", "merge")
    git(other, "push", "--quiet", "origin", "main")

    GitPublisher(repo).sync()
    assert (repo / "merged.txt").exists() and git(repo, "rev-parse", "HEAD") == remote_head(repo)


def test_sync_refuses_a_checkout_on_another_branch(repo: Path) -> None:
    git(repo, "checkout", "--quiet", "-b", "work")
    with pytest.raises(StageFailed, match="checkout is on work, not main"):
        GitPublisher(repo).sync()


def test_a_comment_is_posted_with_gh_on_the_commit_with_the_body_on_stdin(tmp_path: Path) -> None:
    ran: list[tuple[list[str], Path, str | None]] = []

    def record(args, cwd, stdin):
        ran.append((list(args), cwd, stdin))
        return ""

    GitPublisher(tmp_path, run=record).comment("abc123", "## Judge レポート\n")
    ((args, cwd, stdin),) = ran
    assert args == ["gh", "api", "--method", "POST", "repos/{owner}/{repo}/commits/abc123/comments", "-F", "body=@-"]
    assert cwd == tmp_path and stdin == "## Judge レポート\n"


def test_a_failed_command_names_itself_and_redacts_what_it_printed(tmp_path: Path) -> None:
    with pytest.raises(CommandFailed) as raised:
        run_command(["sh", "-c", "echo 'fatal: https://user:ghp_secretsecretsecretsecret@github.com/x' >&2; exit 3"], tmp_path)
    assert raised.value.returncode == 3
    assert raised.value.detail.startswith("sh -c exited 3: fatal:")
    assert "ghp_secret" not in raised.value.detail


def test_the_dry_run_publisher_writes_what_it_would_publish_and_runs_nothing(tmp_path: Path, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("a dry run ran a command")

    monkeypatch.setattr(subprocess, "run", forbidden)
    publisher = DryRunPublisher(tmp_path / "out")
    publisher.sync()
    commit = publisher.publish([tmp_path / "digests" / "2026-09-25.md"], "digest: 2026-09-25")
    publisher.comment(commit, "body\n")

    assert commit == DRY_RUN_COMMIT
    assert (tmp_path / "out" / "commit.txt").read_text(encoding="utf-8").startswith("digest: 2026-09-25\n\n")
    assert (tmp_path / "out" / "comment.md").read_text(encoding="utf-8") == "body\n"
