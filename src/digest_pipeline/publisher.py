"""Where a run touches GitHub: bring the checkout up to date, commit and push, comment.

Every side effect of a run goes through a `Publisher`, so a dry run swaps in
`DryRunPublisher` and nothing leaves the machine. `GitPublisher` uses the local
`git` and `gh` with the credentials already configured on the Mac (#2's
decision), never a token of its own.

`GitPublisher.publish` either pushes a commit or leaves the branch where it
was: when the push fails, the commit is undone and the paths are unstaged, so
the caller can put the files back and the next run starts from the same place.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from digest_contracts import ErrorKind
from digest_observe import StageFailed, safe_detail

COMMAND_TIMEOUT_SECONDS = 120.0

DRY_RUN_COMMIT = "dry-run"
"""What `DryRunPublisher.publish` returns in place of a commit id."""


class Publisher(Protocol):
    def sync(self) -> None:
        """Bring the checkout up to date before the run reads its state."""

    def publish(self, paths: Sequence[Path], message: str) -> str:
        """Commit `paths` with `message`, make the commit public, and return its id."""

    def comment(self, commit: str, body: str) -> None:
        """Post `body` as a comment on `commit`."""


class CommandFailed(StageFailed):
    """A `git` or `gh` command exited non-zero. The detail names the command, not its output."""

    def __init__(self, command: Sequence[str], returncode: int, stderr: str = "") -> None:
        last_line = stderr.strip().splitlines()[-1] if stderr.strip() else ""
        detail = f"{' '.join(command[:2])} exited {returncode}" + (f": {last_line}" if last_line else "")
        super().__init__(ErrorKind.UNEXPECTED, safe_detail(detail))
        self.command = tuple(command)
        self.returncode = returncode


Runner = Callable[[Sequence[str], Path, "str | None"], str]
"""Run a command in a directory with optional stdin and return its stdout. Raises `CommandFailed`."""


def run_command(args: Sequence[str], cwd: Path, stdin: str | None = None) -> str:
    try:
        completed = subprocess.run(
            list(args),
            cwd=cwd,
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise StageFailed(ErrorKind.TIMEOUT, f"{' '.join(args[:2])} timed out") from exc
    if completed.returncode != 0:
        raise CommandFailed(args, completed.returncode, completed.stderr)
    return completed.stdout


@dataclass
class GitPublisher:
    repo: Path
    remote: str = "origin"
    branch: str = "main"
    run: Runner = run_command

    def sync(self) -> None:
        """Fast-forward the branch. A checkout on another branch, or one that diverged, fails the run."""
        current = self._git("rev-parse", "--abbrev-ref", "HEAD").strip()
        if current != self.branch:
            raise StageFailed(ErrorKind.UNEXPECTED, f"checkout is on {current}, not {self.branch}")
        self._git("pull", "--ff-only", self.remote, self.branch)

    def publish(self, paths: Sequence[Path], message: str) -> str:
        relative = [str(Path(path).resolve().relative_to(self.repo.resolve())) for path in paths]
        self._git("add", "--", *relative)
        # `--` with paths commits only these files, whatever else is staged in the checkout.
        self._git("commit", "--quiet", "-m", message, "--", *relative)
        try:
            self._git("push", "--quiet", self.remote, f"HEAD:{self.branch}")
        except StageFailed:
            self._git("reset", "--soft", "HEAD~1")
            self._git("reset", "--quiet", "--", *relative)
            raise
        return self._git("rev-parse", "HEAD").strip()

    def comment(self, commit: str, body: str) -> None:
        # `{owner}/{repo}` is filled in by gh from the checkout's remote; the body goes in on stdin.
        self.run(
            ["gh", "api", "--method", "POST", f"repos/{{owner}}/{{repo}}/commits/{commit}/comments", "-F", "body=@-"],
            self.repo,
            body,
        )

    def _git(self, *args: str) -> str:
        return self.run(["git", *args], self.repo, None)


@dataclass
class DryRunPublisher:
    """Records what a run would publish under `out_dir`, and runs no command."""

    out_dir: Path
    published: list[tuple[tuple[Path, ...], str]] = field(default_factory=list)
    comments: list[tuple[str, str]] = field(default_factory=list)

    def sync(self) -> None:
        return None

    def publish(self, paths: Sequence[Path], message: str) -> str:
        self.published.append((tuple(paths), message))
        self.out_dir.mkdir(parents=True, exist_ok=True)
        listing = "\n".join(str(path) for path in paths)
        (self.out_dir / "commit.txt").write_text(f"{message}\n\n{listing}\n", encoding="utf-8")
        return DRY_RUN_COMMIT

    def comment(self, commit: str, body: str) -> None:
        self.comments.append((commit, body))
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "comment.md").write_text(body, encoding="utf-8")
