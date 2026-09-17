"""The package surface that later issues import."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import digest_contracts

PACKAGE_DIR = Path(digest_contracts.__file__).parent


def test_all_names_resolve_and_are_unique() -> None:
    names = digest_contracts.__all__
    assert len(names) == len(set(names))
    for name in names:
        assert getattr(digest_contracts, name) is not None


def test_no_sdk_or_network_dependency_is_imported() -> None:
    # A fresh interpreter, so modules loaded by pytest plugins do not count.
    code = "import sys, digest_contracts; print('\\n'.join(sys.modules))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    loaded = set(result.stdout.split())
    forbidden = {"claude_agent_sdk", "anthropic", "httpx", "requests", "urllib.request"}
    assert loaded.isdisjoint(forbidden)
    assert "digest_contracts" in loaded


def test_sources_do_not_mention_the_sdk() -> None:
    for path in PACKAGE_DIR.glob("*.py"):
        assert "claude_agent_sdk" not in path.read_text(encoding="utf-8"), path.name


def test_research_contracts_are_left_to_issue_12() -> None:
    owned_by_research = {"SourceDocument", "Evidence", "Claim", "EvidencePacket", "ResearchBudget"}
    for module_name in ("digest_contracts", *(f"digest_contracts.{p.stem}" for p in PACKAGE_DIR.glob("*.py"))):
        module = importlib.import_module(module_name)
        assert owned_by_research.isdisjoint(vars(module)), module_name
