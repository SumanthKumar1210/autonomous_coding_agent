"""Which checks verify a change: the repository's own config, or auto-detection.

Checks are always chosen from the **default branch**, never from the agent's
change (``domain.policy`` forbids changing ``.autoforge.toml``). They run in
the sandbox, never in the service process.

1. ``.autoforge.toml`` at the repository root, if present, decides alone::

       [[checks]]
       name = "firmware"
       run = "cmake -S firmware -B build && cmake --build build && ctest --test-dir build"
       timeout = 900          # seconds, optional (default 600)

   ``checks = []`` turns verification off for the repository.

2. Otherwise checks are detected from the file tree:

   - **CMake**: each top-level ``CMakeLists.txt`` (no ``CMakeLists.txt`` in a
     parent folder) is configured, built and tested with CTest.
   - **pytest**: if the repository has ``test_*.py`` / ``*_test.py`` files.
   - **ruff**: if ruff is configured (``ruff.toml``, ``.ruff.toml`` or
     ``[tool.ruff]`` in ``pyproject.toml``).
"""

from __future__ import annotations

import re
import shlex
import tomllib
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

from autoforge.domain.errors import ChecksConfigError
from autoforge.domain.models import CheckSpec

CHECKS_CONFIG = ".autoforge.toml"
"""Repository file that configures the checks (read from the default branch only)."""

DEFAULT_TIMEOUT = 600
"""Per-check timeout in seconds when the config does not set one."""

MAX_TIMEOUT = 1800
"""Largest per-check timeout a repository may configure."""

MAX_CHECKS = 10
"""Most checks per repository."""

MAX_CMAKE_PROJECTS = 3
"""Most auto-detected CMake projects (the shallowest ones win)."""

BUILD_DIR = ".autoforge-build"
"""Folder (inside the sandbox copy of the repository) that CMake builds into."""


def checks_for(paths: Iterable[str], *, config: str | None, pyproject: str | None) -> list[CheckSpec]:
    """Return the checks for a repository.

    Args:
        paths: Every file path in the repository (default branch).
        config: Content of ``.autoforge.toml``, or ``None`` if there is none.
        pyproject: Content of the root ``pyproject.toml``, or ``None``.

    Raises:
        ChecksConfigError: If ``.autoforge.toml`` is present but invalid.
    """
    if config is not None:
        return parse_config(config)
    return detect_checks(paths, pyproject=pyproject)


def parse_config(text: str) -> list[CheckSpec]:
    """Parse ``.autoforge.toml`` into checks.

    Raises:
        ChecksConfigError: On invalid TOML, unknown keys, or out-of-range values.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ChecksConfigError(f"{CHECKS_CONFIG} is not valid TOML: {exc}") from exc
    raw = data.get("checks", [])
    if not isinstance(raw, list):
        raise ChecksConfigError(f"{CHECKS_CONFIG}: 'checks' must be an array of tables ([[checks]])")
    if len(raw) > MAX_CHECKS:
        raise ChecksConfigError(f"{CHECKS_CONFIG}: at most {MAX_CHECKS} checks are allowed, found {len(raw)}")
    checks = [_parse_check(i, item) for i, item in enumerate(raw, start=1)]
    names = [c.name for c in checks]
    if len(set(names)) != len(names):
        raise ChecksConfigError(f"{CHECKS_CONFIG}: check names must be unique")
    return checks


def detect_checks(paths: Iterable[str], *, pyproject: str | None) -> list[CheckSpec]:
    """Detect checks from the file tree (used when there is no ``.autoforge.toml``)."""
    all_paths = sorted(set(paths))
    checks = [_cmake_check(folder) for folder in _cmake_projects(all_paths)]
    names = {PurePosixPath(p).name for p in all_paths}
    if any(re.fullmatch(r"test_\w+\.py|\w+_test\.py", n) for n in names):
        checks.append(CheckSpec("pytest", "python -m pytest -q -p no:cacheprovider"))
    if {"ruff.toml", ".ruff.toml"} & set(all_paths) or (pyproject and re.search(r"^\[tool\.ruff", pyproject, re.M)):
        checks.append(CheckSpec("ruff", "ruff check --no-cache ."))
    return checks


def _parse_check(index: int, item: Any) -> CheckSpec:
    """Validate one ``[[checks]]`` table."""
    where = f"{CHECKS_CONFIG}: check #{index}"
    if not isinstance(item, dict):
        raise ChecksConfigError(f"{where} must be a table")
    unknown = set(item) - {"name", "run", "timeout"}
    if unknown:
        raise ChecksConfigError(f"{where} has unknown keys: {', '.join(sorted(unknown))}")
    name, run, timeout = item.get("name"), item.get("run"), item.get("timeout", DEFAULT_TIMEOUT)
    if not isinstance(name, str) or not 0 < len(name) <= 60:
        raise ChecksConfigError(f"{where}: 'name' must be a string of 1-60 characters")
    if not isinstance(run, str) or not run.strip() or len(run) > 2000:
        raise ChecksConfigError(f"{where} ({name}): 'run' must be a non-empty command of at most 2000 characters")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 0 < timeout <= MAX_TIMEOUT:
        raise ChecksConfigError(f"{where} ({name}): 'timeout' must be 1-{MAX_TIMEOUT} seconds")
    return CheckSpec(name=name, command=run, timeout_seconds=timeout)


def _cmake_projects(paths: list[str]) -> list[str]:
    """Folders holding a top-level ``CMakeLists.txt``, shallowest first ("" is the repository root)."""
    folders = {str(PurePosixPath(p).parent) for p in paths if PurePosixPath(p).name == "CMakeLists.txt"}
    folders = {"" if f == "." else f for f in folders}
    top = [f for f in folders if not any(f != o and (o == "" or f.startswith(o + "/")) for o in folders)]
    return sorted(top, key=lambda f: (f.count("/"), f))[:MAX_CMAKE_PROJECTS]


def _cmake_check(folder: str) -> CheckSpec:
    """Configure, build and run CTest for the CMake project in ``folder``."""
    source = shlex.quote(folder or ".")
    build = shlex.quote(f"{BUILD_DIR}/{folder.replace('/', '_') or 'root'}")
    command = (f"cmake -S {source} -B {build} && cmake --build {build} --parallel 2 && "
               f"ctest --test-dir {build} --output-on-failure")
    return CheckSpec(f"cmake: {folder or '.'}", command, timeout_seconds=900)
