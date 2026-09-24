"""`python -m digest_pipeline`: run the day's digest, or rehearse it with `--dry-run`.

A dry run calls the sources and the model like a real run, but publishes
nothing: it works on a copy of `digests/` and the processing state under
`logs/dry-run/<run_id>/`, runs no `git` or `gh` command, and leaves the digest,
the commit message and the Judge comment there to read.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from digest_collect import collect_all, load_collection_config, make_fetcher
from digest_contracts import RunStatus
from digest_llm import invoke_structured
from digest_normalize import DEFAULT_STATE_PATH, BodyFetcher, fetch_page
from digest_observe import DEFAULT_METRICS_DIR, MetricsStore, load_pricing, new_run_id, utc_now
from digest_research import load_research_budget

from .config import load_models
from .pipeline import Collector, Invoker, Paths, Pipeline, RunResult
from .publisher import DryRunPublisher, GitPublisher

DEFAULT_CONFIG = Path("config/config.json")
DIGESTS_DIR = Path("digests")
JUDGE_LOG_DIR = Path("logs/judge")
DRY_RUN_DIR = Path("logs/dry-run")

OK_STATUSES = frozenset({RunStatus.SUCCEEDED, RunStatus.PARTIALLY_FAILED})
"""A partially failed run published its digest; only the Judge or the comment failed."""


@dataclass(frozen=True)
class Plan:
    pipeline: Pipeline
    run_id: str
    out_dir: Path | None
    """The dry run's directory, or `None` for a real run."""


def build(
    repo: Path,
    *,
    config: Path = DEFAULT_CONFIG,
    dry_run: bool,
    run_id: str | None = None,
    invoke: Invoker = invoke_structured,
    fetch: BodyFetcher = fetch_page,
    collect: Collector | None = None,
) -> Plan:
    """Wire a run from the config in `repo`. The seams after `dry_run` exist for tests."""
    config_path = repo / config
    run_id = run_id or new_run_id()
    if collect is None:
        collection = load_collection_config(config_path)
        fetcher = make_fetcher(collection.http)

        def collect(known_urls: frozenset[str]):
            return collect_all(collection, fetcher=fetcher, now=utc_now(), known_urls=known_urls)

    if dry_run:
        out_dir = repo / DRY_RUN_DIR / run_id
        paths = Paths(out_dir / "digests", out_dir / "state" / DEFAULT_STATE_PATH.name, out_dir / "judge")
        shutil.copytree(repo / DIGESTS_DIR, paths.digests_dir)
        if (repo / DEFAULT_STATE_PATH).exists():
            paths.state_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo / DEFAULT_STATE_PATH, paths.state_path)
        publisher = DryRunPublisher(out_dir)
        store = MetricsStore(out_dir / "metrics")
    else:
        out_dir = None
        paths = Paths(repo / DIGESTS_DIR, repo / DEFAULT_STATE_PATH, repo / JUDGE_LOG_DIR)
        publisher = GitPublisher(repo)
        store = MetricsStore(repo / DEFAULT_METRICS_DIR)

    pipeline = Pipeline(
        collect=collect,
        fetch=fetch,
        invoke=invoke,
        pricing=load_pricing(config_path),
        models=load_models(config_path),
        publisher=publisher,
        paths=paths,
        budget=load_research_budget(config_path),
        store=store,
    )
    return Plan(pipeline=pipeline, run_id=run_id, out_dir=out_dir)


def summary(result: RunResult, out_dir: Path | None) -> str:
    lines = [f"run: {result.run_id}", f"status: {result.status.value}"]
    for label, error in (("error", result.error), ("metrics error", result.metrics_error)):
        if error is not None:
            lines.append(f"{label}: {error.kind.value}" + (f" ({error.detail})" if error.detail else ""))
    if result.commit is not None:
        lines.append(f"commit: {result.commit}")
    if result.digest_path is not None:
        lines.append(f"digest: {result.digest_path}")
    if out_dir is not None:
        lines.append(f"dry-run output: {out_dir}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m digest_pipeline", description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="publish nothing; write the result under logs/dry-run/<run_id>/")
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="digest date (YYYY-MM-DD); today by default")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository root; the current directory by default")
    args = parser.parse_args(argv)

    plan = build(args.repo.resolve(), dry_run=args.dry_run)
    result = plan.pipeline.run(args.date or date.today(), run_id=plan.run_id)
    print(summary(result, plan.out_dir))
    # An error after a finished run is the metrics record failing to be written: report it.
    clean = result.status in OK_STATUSES and result.error is None and result.metrics_error is None
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
