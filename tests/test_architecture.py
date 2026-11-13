"""Enforces the layer dependency rules documented in ARCHITECTURE.md."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "src" / "autoforge"

# layer -> imports it must never make (prefix match on the imported module)
# Web framework and storage drivers belong to the edges: the HTTP service lives in app/, SQLite adapters in
# integrations/ (the LangGraph SQLite checkpointer is created in app/wiring.py).
_WEB = ("fastapi", "starlette", "uvicorn")
_STORAGE = ("sqlite3", "langgraph.checkpoint.sqlite")

FORBIDDEN: dict[str, tuple[str, ...]] = {
    "domain": ("autoforge.ports", "autoforge.agents", "autoforge.workflow", "autoforge.integrations",
               "autoforge.app", "openai", "httpx", "langgraph", *_WEB, *_STORAGE),
    "ports": ("autoforge.agents", "autoforge.workflow", "autoforge.integrations", "autoforge.app", "openai", "httpx",
              "langgraph", *_WEB, *_STORAGE),
    "agents": ("autoforge.workflow", "autoforge.integrations", "autoforge.app", "httpx", "langgraph", *_WEB, *_STORAGE),
    # The workflow may use LangGraph and the deterministic context builder, but never a concrete LLM agent.
    "workflow": ("autoforge.integrations", "autoforge.app", "autoforge.agents.coder", "autoforge.agents.reviewer",
                 "autoforge.agents.llm", "openai", "httpx", *_WEB, *_STORAGE),
    "integrations": ("autoforge.workflow", "autoforge.app", "autoforge.agents", "openai", "langgraph", *_WEB),
}


def _layer(path: Path) -> str:
    """Return the layer name (first path component) of a module inside the package."""
    rel = path.relative_to(PKG)
    return rel.parts[0].removesuffix(".py")


def _imports(path: Path) -> list[str]:
    """Return every module name imported by a Python file (parsed with ast, not executed)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


MODULES = sorted(p for p in PKG.rglob("*.py") if _layer(p) in FORBIDDEN)


@pytest.mark.parametrize("module", MODULES, ids=lambda p: str(p.relative_to(PKG)))
def test_layer_dependencies(module: Path) -> None:
    """No module imports a layer it is not allowed to depend on."""
    forbidden = FORBIDDEN[_layer(module)]
    violations = [name for name in _imports(module) if any(name == f or name.startswith(f + ".") for f in forbidden)]
    assert not violations, f"{module.relative_to(PKG)} imports {violations}"
