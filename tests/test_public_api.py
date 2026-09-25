"""The package surface that later issues import."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel

import agent_daily_digest.contracts

PACKAGE_DIR = Path(agent_daily_digest.contracts.__file__).parent


def test_all_names_resolve_and_are_unique() -> None:
    names = agent_daily_digest.contracts.__all__
    assert len(names) == len(set(names))
    for name in names:
        assert getattr(agent_daily_digest.contracts, name) is not None


def test_every_model_is_reachable_from_the_package_root() -> None:
    """A model a caller can build has to be in `__all__`, not only in its submodule."""
    for path in PACKAGE_DIR.glob("*.py"):
        if path.stem == "__init__":
            continue
        module = importlib.import_module(f"agent_daily_digest.contracts.{path.stem}")
        for name, obj in vars(module).items():
            if name.startswith("_") or not isinstance(obj, type):
                continue
            if issubclass(obj, BaseModel) and obj.__module__ == module.__name__:
                assert name in agent_daily_digest.contracts.__all__, f"{module.__name__}.{name}"


def test_no_sdk_or_network_dependency_is_imported() -> None:
    # A fresh interpreter, so modules loaded by pytest plugins do not count.
    code = "import sys, agent_daily_digest.contracts; print('\\n'.join(sys.modules))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    loaded = set(result.stdout.split())
    forbidden = {"claude_agent_sdk", "anthropic", "httpx", "requests", "urllib.request"}
    assert loaded.isdisjoint(forbidden)
    assert "agent_daily_digest.contracts" in loaded


def test_sources_do_not_mention_the_sdk() -> None:
    for path in PACKAGE_DIR.glob("*.py"):
        assert "claude_agent_sdk" not in path.read_text(encoding="utf-8"), path.name


def test_research_contracts_are_defined_only_in_the_research_module() -> None:
    """#3 left these to #12; #12 put them in one module, exported from the root."""
    owned_by_research = {"SourceDocument", "Evidence", "Claim", "EvidencePacket", "ResearchBudget"}
    for path in PACKAGE_DIR.glob("*.py"):
        module = importlib.import_module(f"agent_daily_digest.contracts.{path.stem}")
        defined_here = {
            name for name in owned_by_research if getattr(getattr(module, name, None), "__module__", None) == module.__name__
        }
        assert defined_here == (owned_by_research if path.stem == "research" else set()), path.stem
    assert owned_by_research <= set(agent_daily_digest.contracts.__all__)
