"""Daily digest application.

The package root stays light so importing a contract or the renderer does not
load the CLI, network code, or model adapter. Public run helpers are resolved
only when requested.
"""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "cli": ("OK_STATUSES", "Plan", "build", "main", "summary"),
    "config": ("Models", "load_models"),
    "pipeline": ("Collector", "Invoker", "Paths", "Pipeline", "RunResult", "comment_body"),
    "publisher": (
        "DRY_RUN_COMMIT", "CommandFailed", "DryRunPublisher", "GitPublisher",
        "Publisher", "Runner", "run_command",
    ),
}
__all__ = [name for names in _EXPORTS.values() for name in names]


def __getattr__(name: str) -> Any:
    for module, names in _EXPORTS.items():
        if name in names:
            value = getattr(import_module(f".{module}", __name__), name)
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
