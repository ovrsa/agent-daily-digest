"""The daily run: every component from collection to the Judge comment, in one flow.

Import from this package, not from its submodules. `Pipeline.run` carries out
one run under the Design Doc's Failure policy and returns a `RunResult`.
Everything that leaves the machine goes through a `Publisher`: `GitPublisher`
for a real run, `DryRunPublisher` for a rehearsal. `python -m digest_pipeline`
is the command line (see `cli.py`).
"""

from .cli import OK_STATUSES, Plan, build, main, summary
from .config import Models, load_models
from .pipeline import Collector, Invoker, Paths, Pipeline, RunResult, comment_body
from .publisher import (
    DRY_RUN_COMMIT,
    CommandFailed,
    DryRunPublisher,
    GitPublisher,
    Publisher,
    Runner,
    run_command,
)

__all__ = [
    "DRY_RUN_COMMIT",
    "OK_STATUSES",
    "Collector",
    "CommandFailed",
    "DryRunPublisher",
    "GitPublisher",
    "Invoker",
    "Models",
    "Paths",
    "Pipeline",
    "Plan",
    "Publisher",
    "RunResult",
    "Runner",
    "build",
    "comment_body",
    "load_models",
    "main",
    "run_command",
    "summary",
]
