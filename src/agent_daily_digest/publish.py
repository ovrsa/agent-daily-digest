"""Where a run touches GitHub: bring the checkout up to date, commit and push, comment.

Digest, index and state writes are coordinated by `publish_selection`.
External side effects of a run go through a `Publisher`, so a dry run swaps in
`DryRunPublisher` and nothing leaves the machine. `GitPublisher` uses the local
`git` and `gh` with the credentials already configured on the Mac (#2's
decision), never a token of its own.

`GitPublisher.publish` either pushes a commit or leaves the branch where it
was: when anything stops it before the push completes, an interrupt included,
the commit is undone and the paths are unstaged, so the caller can put the
files back and the next run starts from the same place. An interrupt that
lands after the remote accepted the push leaves the commit on the remote only;
the next run's fast-forward brings it back.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol

from agent_daily_digest.contracts.articles import NormalizedArticle, ProcessedState
from agent_daily_digest.contracts.base import ErrorKind
from agent_daily_digest.contracts.editorial import SelectorOutput
from agent_daily_digest.observe.recorder import StageFailed
from agent_daily_digest.observe.store import safe_detail
from agent_daily_digest.render.digest import digest_filename, render_digest
from agent_daily_digest.render.index import README_FILENAME, update_index
from agent_daily_digest.state import save_state

COMMAND_TIMEOUT_SECONDS = 120.0

DRY_RUN_COMMIT = "dry-run"
"""What `DryRunPublisher.publish` returns in place of a commit id."""


_DIGEST_FILENAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


@dataclass(frozen=True)
class Publication:
    commit: str
    digest_path: Path | None


def publish_selection(
    output: SelectorOutput,
    articles: Mapping[str, NormalizedArticle],
    decided: ProcessedState,
    digest_date: date,
    *,
    digests_dir: Path,
    state_path: Path,
    publisher: Publisher,
    adopted: bool,
) -> Publication:
    """Write the digest/index and decided state, publish them, and restore files on any failure."""
    digest_path = digests_dir / digest_filename(digest_date)
    written = _Snapshot.of(digest_path, digests_dir / README_FILENAME, state_path)
    try:
        files: list[Path] = []
        if adopted:
            digest = write_digest(output, articles, digest_date, digests_dir)
            files += [digest, digests_dir / README_FILENAME]
        save_state(state_path, decided)
        files.append(state_path)
        kind = "digest" if adopted else "state"
        commit = publisher.publish(files, f"{kind}: {digest_date.isoformat()}")
    except BaseException:
        written.restore()
        raise
    return Publication(commit, digest_path if adopted else None)


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
        relative = [self._relative(path) for path in paths]
        before = self._head()
        try:
            self._git("add", "--", *relative)
            # `--` with paths commits only these files, whatever else is staged in the checkout.
            self._git("commit", "--quiet", "-m", message, "--", *relative)
            self._git("push", "--quiet", self.remote, f"HEAD:{self.branch}")
        except BaseException:
            # Whatever stopped the push, an interrupt included, a commit left behind would
            # ride along with the next run's push. Return to where the branch was.
            if self._head() != before:
                self._git("reset", "--soft", before)
            self._git("reset", "--quiet", "--", *relative)
            raise
        return self._head()

    def comment(self, commit: str, body: str) -> None:
        # `{owner}/{repo}` is filled in by gh from the checkout's remote; the body goes in on stdin.
        self.run(
            ["gh", "api", "--method", "POST", f"repos/{{owner}}/{{repo}}/commits/{commit}/comments", "-F", "body=@-"],
            self.repo,
            body,
        )

    def _git(self, *args: str) -> str:
        return self.run(["git", *args], self.repo, None)

    def _head(self) -> str:
        return self._git("rev-parse", "HEAD").strip()

    def _relative(self, path: Path) -> str:
        try:
            return str(Path(path).resolve().relative_to(self.repo.resolve()))
        except ValueError as exc:
            raise StageFailed(ErrorKind.UNEXPECTED, "a path to publish is outside the repository") from exc


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


def list_digest_dates(digests_dir: Path) -> tuple[date, ...]:
    """Dates of the digest files in `digests_dir`, newest first.

    Anything that is not a `YYYY-MM-DD.md` naming a real date is ignored, so
    `README.md` and stray files do not reach the index.
    """
    found = []
    for path in digests_dir.iterdir():
        match = _DIGEST_FILENAME.match(path.name)
        if match is None or not path.is_file():
            continue
        try:
            found.append(date.fromisoformat(match.group(1)))
        except ValueError:
            continue
    return tuple(sorted(found, reverse=True))


def write_digest(
    selector_output: SelectorOutput,
    articles: Mapping[str, NormalizedArticle],
    digest_date: date,
    digests_dir: Path,
) -> Path | None:
    """Write a checked digest and refresh the index; write nothing if none was adopted."""
    markdown = render_digest(selector_output, articles, digest_date)
    if markdown is None:
        return None

    readme_path = digests_dir / README_FILENAME
    readme_text = update_index(
        readme_path.read_text(encoding="utf-8"),
        (digest_date, *list_digest_dates(digests_dir)),
    )

    digest_path = digests_dir / digest_filename(digest_date)
    digest_path.write_bytes(markdown.encode("utf-8"))
    readme_path.write_bytes(readme_text.encode("utf-8"))
    return digest_path


@dataclass(frozen=True)
class _Snapshot:
    """The bytes of some files before a publish wrote them, to put back if it fails."""

    before: tuple[tuple[Path, bytes | None], ...]

    @classmethod
    def of(cls, *paths: Path) -> _Snapshot:
        return cls(tuple((path, path.read_bytes() if path.exists() else None) for path in paths))

    def restore(self) -> None:
        for path, content in self.before:
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
