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

from .recorder import Clock, utc_now
from .safety import MetricsLeakError, find_leaks

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
