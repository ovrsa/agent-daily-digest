"""Issue #42's module layout, direct imports and acyclic startup dependencies."""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from graphlib import TopologicalSorter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "agent_daily_digest"
EXPECTED = {
    "__init__.py", "__main__.py", "cli.py", "config.py", "pipeline.py", "state.py", "normalize.py",
    "collect/__init__.py", "collect/run.py", "collect/connectors.py", "collect/transport.py",
    "content/__init__.py", "content/fetch.py", "content/extract.py", "content/urls.py", "content/text.py",
    "research/__init__.py", "research/run.py", "research/evidence.py", "research/extraction.py", "research/prompt.py",
    "select.py", "select_prompt.py", "judge.py", "judge_prompt.py",
    "render/__init__.py", "render/digest.py", "render/index.py", "publish.py",
    "llm/__init__.py", "llm/client.py", "llm/call.py", "llm/pricing.py",
    "observe/__init__.py", "observe/recorder.py", "observe/store.py", "observe/summary.py",
    "contracts/__init__.py", "contracts/base.py", "contracts/articles.py", "contracts/research.py",
    "contracts/editorial.py", "contracts/metrics.py",
}


def module_name(path: Path) -> str:
    parts = path.relative_to(ROOT).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("agent_daily_digest", *parts))


def test_the_modules_match_the_agreed_layout() -> None:
    assert {str(path.relative_to(ROOT)) for path in ROOT.rglob("*.py")} == EXPECTED


def test_startup_imports_are_direct_and_acyclic() -> None:
    namespaces = {module_name(path) for path in ROOT.rglob("__init__.py")}
    graph = {}
    for path in ROOT.rglob("*.py"):
        name = module_name(path)
        tree = ast.parse(path.read_text())
        dependencies = set()
        # Imports inside functions and TYPE_CHECKING do not run at startup.
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                target = node.module or ""
                if node.level:
                    package = name if path.name == "__init__.py" else name.rsplit(".", 1)[0]
                    target = importlib.util.resolve_name("." * node.level + target, package)
                if target.startswith("agent_daily_digest"):
                    assert target not in namespaces, (name, target)
                    dependencies.add(target)
        graph[name] = dependencies
    TopologicalSorter(graph).prepare()


def test_all_implementation_modules_import_in_a_fresh_interpreter() -> None:
    names = sorted(
        (module_name(path) for path in ROOT.rglob("*.py") if path.name != "__main__.py"),
        reverse=True,
    )
    code = f"import importlib; [importlib.import_module(name) for name in {names!r}]"
    subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
