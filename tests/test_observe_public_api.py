"""The package surfaces of `agent_daily_digest.observe` and `agent_daily_digest.llm.client`, and the SDK boundary between them."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import agent_daily_digest.llm.client
import agent_daily_digest.observe


@pytest.mark.parametrize("package", [agent_daily_digest.observe, agent_daily_digest.llm.client])
def test_all_names_resolve_and_are_unique(package) -> None:
    names = package.__all__
    assert len(names) == len(set(names))
    for name in names:
        assert getattr(package, name) is not None


def loaded_modules(package: str) -> set[str]:
    code = f"import sys, {package}; print('\\n'.join(sys.modules))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return set(result.stdout.split())


def test_observe_never_imports_the_sdk() -> None:
    assert "claude_agent_sdk" not in loaded_modules("agent_daily_digest.observe")
    source = "".join(p.read_text(encoding="utf-8") for p in Path(agent_daily_digest.observe.__file__).parent.glob("*.py"))
    assert "claude_agent_sdk" not in source


def test_importing_the_adapter_does_not_load_the_sdk() -> None:
    assert "claude_agent_sdk" not in loaded_modules("agent_daily_digest.llm.client")
