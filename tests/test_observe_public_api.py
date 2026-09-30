"""The package surfaces of `agent_daily_digest.observe` and `agent_daily_digest.llm.client`, and the SDK boundary between them."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import agent_daily_digest.llm
import agent_daily_digest.llm.client
import agent_daily_digest.observe


@pytest.mark.parametrize("package", [agent_daily_digest.observe, agent_daily_digest.llm])
def test_packages_are_thin_namespaces(package) -> None:
    import ast

    tree = ast.parse(Path(package.__file__).read_text())
    assert all(isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) for node in tree.body)


def loaded_modules(package: str) -> set[str]:
    code = f"import sys, {package}; print('\\n'.join(sys.modules))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return set(result.stdout.split())


def test_observe_never_imports_the_sdk() -> None:
    assert "claude_agent_sdk" not in loaded_modules("agent_daily_digest.observe.recorder")
    source = "".join(p.read_text(encoding="utf-8") for p in Path(agent_daily_digest.observe.__file__).parent.glob("*.py"))
    assert "claude_agent_sdk" not in source


def test_importing_the_adapter_does_not_load_the_sdk() -> None:
    assert "claude_agent_sdk" not in loaded_modules("agent_daily_digest.llm.client")


def test_only_the_client_mentions_the_sdk() -> None:
    llm_dir = Path(agent_daily_digest.llm.__file__).parent
    for path in llm_dir.glob("*.py"):
        if path.stem != "client":
            assert "claude_agent_sdk" not in path.read_text(), path.name
    assert "claude_agent_sdk" not in loaded_modules("agent_daily_digest.llm.call")
