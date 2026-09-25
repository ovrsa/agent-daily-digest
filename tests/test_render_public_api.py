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


def test_all_names_resolve_and_are_unique() -> None:
    names = agent_daily_digest.render.__all__
    assert len(names) == len(set(names))
    for name in names:
        assert getattr(agent_daily_digest.render, name) is not None


def test_everything_public_in_a_submodule_is_reachable_from_the_package_root() -> None:
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        if path.stem == "__init__":
            continue
        module = importlib.import_module(f"agent_daily_digest.render.{path.stem}")
        for name, obj in vars(module).items():
            if name.startswith("_") or getattr(obj, "__module__", None) != module.__name__:
                continue
            assert name in agent_daily_digest.render.__all__, f"{module.__name__}.{name}"


def test_importing_the_renderer_loads_no_model_or_network_module() -> None:
    """The digest is assembled, not written by a model. Nothing here talks to one."""
    # A fresh interpreter, so modules loaded by pytest plugins do not count.
    code = "import sys, agent_daily_digest.render; print('\\n'.join(sys.modules))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    loaded = set(result.stdout.split())
    assert loaded.isdisjoint(LLM_MODULES)
    assert "agent_daily_digest.render" in loaded


def test_rendering_a_digest_loads_no_model_or_network_module() -> None:
    code = (
        "import sys, json, datetime, pathlib\n"
        f"sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
        "import render_factories as f\n"
        "from agent_daily_digest.render import render_digest\n"
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
