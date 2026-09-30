"""The package surface that later issues import."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel

import agent_daily_digest.contracts

PACKAGE_DIR = Path(agent_daily_digest.contracts.__file__).parent


def test_contract_package_is_a_thin_namespace() -> None:
    import ast

    tree = ast.parse(Path(agent_daily_digest.contracts.__file__).read_text())
    assert all(isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) for node in tree.body)


def test_every_model_is_reachable_from_its_implementation_module() -> None:
    for path in PACKAGE_DIR.glob("*.py"):
        if path.stem == "__init__":
            continue
        module = importlib.import_module(f"agent_daily_digest.contracts.{path.stem}")
        for name, obj in vars(module).items():
            if name.startswith("_") or not isinstance(obj, type):
                continue
            if issubclass(obj, BaseModel) and obj.__module__ == module.__name__:
                assert getattr(importlib.import_module(obj.__module__), name) is obj


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
    research = importlib.import_module("agent_daily_digest.contracts.research")
    assert all(getattr(research, name).__module__ == research.__name__ for name in owned_by_research)
