"""Run metrics kept on the local disk until the weekly review.

The #2 spike settled where: a directory outside Git on the Mac that runs the
job. `logs/` is already in `.gitignore`, so the default lives under it. One
JSON file per run, named by start time so the directory sorts chronologically
and pruning needs no index.

Retention defaults (decided in #9, see the parent issue's Open issues):

- `RETENTION_DAYS = 35`: the review is weekly; five weeks keeps a missed review
  recoverable at the next one
- `MAX_RUN_FILES = 120`: a daily job with manual reruns stays under it for the
  whole retention window, and a runaway loop cannot fill the disk
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from agent_daily_digest.contracts import RunMetrics
from collections.abc import Callable

from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass
from typing import Any
from agent_daily_digest.contracts import ERROR_DETAIL_MAX_CHARS


Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)



DEFAULT_METRICS_DIR = Path("logs/metrics")
RETENTION_DAYS = 35
MAX_RUN_FILES = 120

_FILENAME = re.compile(r"^(\d{8}T\d{6}Z)_([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})\.json$")
_STAMP = "%Y%m%dT%H%M%SZ"


def metrics_filename(run: RunMetrics) -> str:
    started = run.started_at.astimezone(timezone.utc)
    # `:` is legal in a run id but not in every filesystem's names.
    return f"{started:{_STAMP}}_{run.run_id.replace(':', '-')}.json"


class MetricsStore:
    def __init__(
        self,
        directory: Path | str = DEFAULT_METRICS_DIR,
        *,
        retention_days: int = RETENTION_DAYS,
        max_files: int = MAX_RUN_FILES,
        clock: Clock = utc_now,
    ) -> None:
        if retention_days < 1 or max_files < 1:
            raise ValueError("retention_days and max_files must be at least 1")
        self.directory = Path(directory)
        self.retention_days = retention_days
        self.max_files = max_files
        self._clock = clock

    def write(self, run: RunMetrics, *, sensitive: Iterable[str] = ()) -> Path:
        """Write one run, after checking it carries no secret and no copied text.

        Raises `MetricsLeakError` and writes nothing when the check fails. The
        file is written to a temporary name and renamed, so a reader never sees
        half a record. Old files are pruned after the write.
        """
        payload = run.model_dump(mode="json")
        leaks = find_leaks(payload, sensitive)
        if leaks:
            raise MetricsLeakError(leaks)
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / metrics_filename(run)
        handle, temporary = tempfile.mkstemp(dir=self.directory, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(run.model_dump_json(indent=2))
                stream.write("\n")
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        self.prune()
        return target

    def paths(self) -> tuple[Path, ...]:
        """Run files, oldest first. Files this store did not name are ignored."""
        if not self.directory.is_dir():
            return ()
        return tuple(sorted(p for p in self.directory.iterdir() if p.is_file() and _FILENAME.match(p.name)))

    def prune(self) -> tuple[Path, ...]:
        """Delete run files older than the retention window, then the oldest over the cap."""
        cutoff = self._clock().astimezone(timezone.utc) - timedelta(days=self.retention_days)
        removed = []
        kept = []
        for path in self.paths():
            if _started_at(path) < cutoff:
                path.unlink(missing_ok=True)
                removed.append(path)
            else:
                kept.append(path)
        for path in kept[: max(0, len(kept) - self.max_files)]:
            path.unlink(missing_ok=True)
            removed.append(path)
        return tuple(removed)

    def load(self, since: datetime | None = None) -> tuple[RunMetrics, ...]:
        """Runs started at or after `since`, oldest first."""
        runs = []
        for path in self.paths():
            if since is not None and _started_at(path) < since.astimezone(timezone.utc):
                continue
            runs.append(RunMetrics.model_validate_json(path.read_text(encoding="utf-8")))
        return tuple(runs)


def _started_at(path: Path) -> datetime:
    match = _FILENAME.match(path.name)
    assert match is not None
    return datetime.strptime(match.group(1), _STAMP).replace(tzinfo=timezone.utc)


LEAK_WINDOW_CHARS = 64
"""A run of this many characters shared with a sensitive text counts as a copy.

Short enough to catch a quoted sentence, long enough that a product name
mentioned by an article does not trip it. Whitespace is collapsed first. A
sensitive text shorter than this is matched whole instead.
"""

URL_FIELDS = frozenset({"canonical_url"})
"""Fields the contracts type as a URL, whose value is exempt from the copy check.

An article's address is public metadata, and a page that prints another
article's full address would otherwise refuse the whole record (seen on
2026-09-25 with claude.com posts). The exemption goes by field, not by shape:
a free-text field such as `decision_reason` stays checked even when its value
looks like a URL, since body text turned into a slug would pass any shape test.
Exempt values are still checked for secrets.
"""

LEAK_MIN_CHARS = 16
"""A sensitive text shorter than this is not checked at all.

A match of a few characters says nothing about copying: `"a"` occurs in every
run id and URL, and flagging it would refuse the whole record. Bodies and
prompts, the texts marked sensitive, are far longer than this.
"""

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("oauth_env", re.compile(r"CLAUDE_CODE_OAUTH_TOKEN\s*=\s*\S+")),
    # Up to the last `@` before the path: a password may itself contain `@`, `#` or `?`.
    ("url_credentials", re.compile(r"https?://[^/\s@:]+:[^/\s]*@")),
)
"""Credentials this project can hold: model auth, `gh` auth, and URLs with userinfo."""

_REDACTED = "<redacted>"

_URL_ONLY = re.compile(r"https?://\S+")


def redact_secrets(text: str) -> str:
    """Replace every match of `SECRET_PATTERNS` with a fixed marker."""
    for _name, pattern in SECRET_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


def safe_detail(text: str | None) -> str | None:
    """A detail string fit for `ErrorRecord.detail`: redacted, one line, capped."""
    if text is None:
        return None
    collapsed = " ".join(redact_secrets(text).split())
    return collapsed[:ERROR_DETAIL_MAX_CHARS] or None


def describe_exception(exc: BaseException) -> str:
    """Name an exception without quoting its message.

    SDK and parser exceptions put the offending input in their message: a JSON
    decode error quotes the model's output, a process error quotes the CLI's
    stderr. The class name and, when present, a numeric status are enough to act on.
    """
    parts = [type(exc).__name__]
    for attribute in ("status_code", "exit_code", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int) and not isinstance(value, bool):
            parts.append(f"{attribute}={value}")
    return " ".join(parts)


@dataclass(frozen=True)
class Leak:
    """Where a forbidden text was found. The text itself is not repeated."""

    location: str
    kind: str
    """`secret:<pattern name>` or `sensitive_text`."""


class MetricsLeakError(ValueError):
    """A record was about to be written with a secret or a copied body in it."""

    def __init__(self, leaks: tuple[Leak, ...]) -> None:
        self.leaks = leaks
        super().__init__(
            "refusing to write metrics: " + ", ".join(f"{leak.location} ({leak.kind})" for leak in leaks)
        )


def find_leaks(
    payload: Any,
    sensitive: Iterable[str] = (),
    *,
    window: int = LEAK_WINDOW_CHARS,
    url_fields: Collection[str] = URL_FIELDS,
) -> tuple[Leak, ...]:
    """Every string in `payload` that holds a secret or a copy of a sensitive text.

    `payload` is JSON-shaped data (what `model_dump(mode="json")` returns). A
    copy is a verbatim run of text; a paraphrase, or text re-encoded (spaces
    turned into hyphens, percent-encoding), is not detected.
    """
    strings = list(_strings(payload, "$"))
    leaks: list[Leak] = []
    for location, value in strings:
        for name, pattern in SECRET_PATTERNS:
            if pattern.search(value):
                leaks.append(Leak(location=location, kind=f"secret:{name}"))
                break

    copyable = [(location, value) for location, value in strings if not _is_url_field(location, value, url_fields)]
    windows: dict[str, str] = {}
    for location, value in copyable:
        text = _collapse(value)
        for start in range(0, max(0, len(text) - window + 1)):
            windows.setdefault(text[start : start + window], location)
    flagged: set[str] = set()

    def flag(location: str) -> None:
        if location not in flagged:
            flagged.add(location)
            leaks.append(Leak(location=location, kind="sensitive_text"))

    for source in sensitive:
        text = _collapse(source)
        if len(text) < LEAK_MIN_CHARS:
            continue
        if len(text) < window:
            # Too short to slide a window over, so it must not appear at all.
            for location, value in copyable:
                if text in _collapse(value):
                    flag(location)
            continue
        for start in range(0, len(text) - window + 1):
            location = windows.get(text[start : start + window])
            if location is not None:
                flag(location)
    return tuple(leaks)


def _is_url_field(location: str, value: str, url_fields: Collection[str]) -> bool:
    return location.rsplit(".", 1)[-1] in url_fields and _URL_ONLY.fullmatch(value) is not None


def _strings(value: Any, location: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield location, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, f"{location}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _strings(item, f"{location}[{index}]")


def _collapse(text: str) -> str:
    return " ".join(text.split())
