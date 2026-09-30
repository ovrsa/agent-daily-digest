"""The renderer's package surface, and the guarantee that it calls no model."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel

import agent_daily_digest.render

PACKAGE_DIR = Path(agent_daily_digest.render.__file__).parent

# `socket` is left out on purpose: Pydantic loads it, so it says nothing about
# this package. The entries below are model clients and HTTP clients.
LLM_MODULES = {
    "claude_agent_sdk",
    "anthropic",
    "openai",
    "httpx",
    "requests",
    "urllib.request",
    "http.client",
}


def test_renderer_package_is_a_thin_namespace() -> None:
    import ast

    tree = ast.parse(Path(agent_daily_digest.render.__file__).read_text())
    assert all(isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) for node in tree.body)


def test_render_modules_contain_no_file_writes() -> None:
    import ast

    for path in PACKAGE_DIR.glob("*.py"):
        tree = ast.parse(path.read_text())
        writes = {"write_text", "write_bytes", "open", "mkdir", "unlink", "replace"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                # str.replace is text formatting, not a filesystem replacement.
                assert node.func.attr not in writes - {"replace"}, path.name


def test_importing_the_renderer_loads_no_model_or_network_module() -> None:
    """The digest is assembled, not written by a model. Nothing here talks to one."""
    # A fresh interpreter, so modules loaded by pytest plugins do not count.
    code = "import sys, agent_daily_digest.render.digest, agent_daily_digest.render.index; print('\\n'.join(sys.modules))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    loaded = set(result.stdout.split())
    assert loaded.isdisjoint(LLM_MODULES)
    assert "agent_daily_digest.render" in loaded


def test_rendering_a_digest_loads_no_model_or_network_module() -> None:
    code = (
        "import sys, json, datetime, pathlib\n"
        f"sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
        "import render_factories as f\n"
        "from agent_daily_digest.render.digest import render_digest\n"
        "render_digest(f.selector_output(), f.articles(), datetime.date(2026, 9, 18))\n"
        "print('\\n'.join(sys.modules))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert set(result.stdout.split()).isdisjoint(LLM_MODULES)


def test_no_source_mentions_a_model_client() -> None:
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for name in ("claude_agent_sdk", "anthropic.", "import anthropic", "openai"):
            assert name not in source, f"{path.name}: {name}"


def test_the_renderer_does_not_redefine_a_contract() -> None:
    """The contracts are fixed in #3; the renderer imports them, it does not restate them."""
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        module = importlib.import_module(f"agent_daily_digest.render.{path.stem}")
        defined = [
            name
            for name, obj in vars(module).items()
            if isinstance(obj, type)
            and issubclass(obj, BaseModel)
            and obj.__module__ == module.__name__
        ]
        assert defined == [], f"{module.__name__}: {defined}"
