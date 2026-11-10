"""Enforces the layer dependency rules documented in ARCHITECTURE.md."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "src" / "autoforge"

# layer -> imports it must never make (prefix match on the imported module)
FORBIDDEN: dict[str, tuple[str, ...]] = {
    "domain": ("autoforge.ports", "autoforge.agents", "autoforge.workflow", "autoforge.integrations",
               "autoforge.app", "openai", "httpx"),
    "ports": ("autoforge.agents", "autoforge.workflow", "autoforge.integrations", "autoforge.app", "openai", "httpx"),
    "agents": ("autoforge.workflow", "autoforge.integrations", "autoforge.app", "httpx"),
    "workflow": ("autoforge.integrations", "autoforge.app", "autoforge.agents.coder", "openai", "httpx"),
    "integrations": ("autoforge.workflow", "autoforge.app", "autoforge.agents", "openai"),
}


def _layer(path: Path) -> str:
    rel = path.relative_to(PKG)
    return rel.parts[0].removesuffix(".py")


def _imports(path: Path) -> list[str]:
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
    forbidden = FORBIDDEN[_layer(module)]
    violations = [name for name in _imports(module) if any(name == f or name.startswith(f + ".") for f in forbidden)]
    assert not violations, f"{module.relative_to(PKG)} imports {violations}"
