"""Tests for domain.checks: which checks verify a change (config file or auto-detection)."""

from __future__ import annotations

import pytest

from autoforge.domain.checks import checks_for, detect_checks, parse_config
from autoforge.domain.errors import ChecksConfigError


def test_detects_top_level_cmake_projects_pytest_and_ruff() -> None:
    """Nested CMakeLists.txt files belong to their parent project; tests and ruff config add checks."""
    paths = ["firmware/CMakeLists.txt", "firmware/test/CMakeLists.txt", "firmware/src/a.c", "tools/x/CMakeLists.txt",
             "utils/test_soc.py", "utils/soc.py", "pyproject.toml"]

    checks = detect_checks(paths, pyproject="[project]\nname='x'\n[tool.ruff]\nline-length = 100\n")

    assert [c.name for c in checks] == ["cmake: firmware", "cmake: tools/x", "pytest", "ruff"]
    cmake = checks[0].command
    assert "cmake -S firmware -B .autoforge-build/firmware" in cmake and "ctest --test-dir" in cmake
    assert checks[0].timeout_seconds == 900


def test_root_cmake_project_and_nothing_else() -> None:
    """A root CMakeLists.txt covers the whole tree; no Python tests and no ruff config means no Python checks."""
    checks = detect_checks(["CMakeLists.txt", "sub/CMakeLists.txt", "src/main.c", "tool.py"], pyproject=None)

    assert [c.name for c in checks] == ["cmake: ."]
    assert "-S . -B .autoforge-build/root" in checks[0].command


def test_no_checks_detected() -> None:
    """A repository without build files or tests has no checks."""
    assert detect_checks(["README.md", "src/app.py"], pyproject=None) == []


def test_config_file_replaces_detection() -> None:
    """``.autoforge.toml`` decides alone; ``checks = []`` disables verification."""
    config = '[[checks]]\nname = "unit"\nrun = "make test"\ntimeout = 120\n'

    checks = checks_for(["CMakeLists.txt", "test_x.py"], config=config, pyproject=None)

    assert [(c.name, c.command, c.timeout_seconds) for c in checks] == [("unit", "make test", 120)]
    assert checks_for(["CMakeLists.txt"], config="checks = []\n", pyproject=None) == []


@pytest.mark.parametrize(("config", "message"), [
    ("[[checks]\n", "not valid TOML"),
    ('checks = "make"\n', "array of tables"),
    ('[[checks]]\nname = "a"\n', "'run'"),
    ('[[checks]]\nname = "a"\nrun = "x"\ntimeout = 99999\n', "'timeout'"),
    ('[[checks]]\nname = "a"\nrun = "x"\nshell = "bash"\n', "unknown keys: shell"),
    ('[[checks]]\nname = "a"\nrun = "x"\n[[checks]]\nname = "a"\nrun = "y"\n', "unique"),
])
def test_invalid_config_is_rejected_with_a_clear_message(config: str, message: str) -> None:
    """Every invalid configuration names the problem."""
    with pytest.raises(ChecksConfigError, match=message):
        parse_config(config)
