"""Load the evaluation suite: fixture repositories and cases.

Layout of a suite folder::

    evals/
    ├── repos/<repo>/...              frozen fixture repositories
    └── cases/<case-id>/
        ├── case.toml                 the ticket and what is expected (format below)
        ├── hidden/...                files added only when grading (acceptance tests), at their repo paths
        └── solution/...              reference change: the files it creates or modifies, at their repo paths

``case.toml``::

    repo = "bms_c"                    # folder under repos/
    title = "Detect cell imbalance"   # ticket title
    body = "..."                      # ticket body (acceptance criteria)

    [expect]
    context = ["src/a.c"]             # must be shown to the agents (context recall)
    changed = ["src/a.c"]             # the change must create or modify these
    unchanged = ["include/hal.h"]     # the change must not touch these
    contains = [{ path = "src/a.h", pattern = 'regex' }]   # must match the file after the change

    [[hidden_checks]]                 # run on the change + hidden files; must pass
    name = "acceptance"
    run = "python -m pytest -q .autoforge-eval"
    timeout = 300                     # seconds, optional

Hidden files and the solution are never shown to the agents.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from autoforge.domain.checks import MAX_TIMEOUT
from autoforge.domain.models import CheckSpec, Ticket

HIDDEN_CHECK_PREFIX = "hidden: "
"""Prefix of hidden check names, so their results can be told apart from the repository's own checks."""

_CASE_KEYS = {"repo", "title", "body", "expect", "hidden_checks"}
_EXPECT_KEYS = {"context", "changed", "unchanged", "contains"}
_CASE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_SKIP_DIRS = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git"})
"""Tool caches (and git metadata) left in a fixture by local runs; never part of the suite."""


class SuiteError(ValueError):
    """The suite or one of its cases is malformed."""


@dataclass(frozen=True)
class FilePattern:
    """A regular expression that must match a file's content after the change.

    Attributes:
        path: Repository-relative path.
        pattern: Python regular expression (``re.search``).
    """

    path: str
    pattern: str


@dataclass(frozen=True)
class EvalCase:
    """One sample ticket and how to grade a change for it.

    Attributes:
        id: Case folder name, e.g. ``"c-soc-clamp"``; also the ticket key.
        repo: Fixture repository name (folder under ``repos/``).
        repo_dir: Path of the fixture repository.
        title: Ticket title.
        body: Ticket body.
        expect_context: Files the context must include.
        expect_changed: Files the change must create or modify.
        expect_unchanged: Files the change must not touch.
        contains: Patterns the changed repository must match.
        hidden_checks: Acceptance checks (names start with ``HIDDEN_CHECK_PREFIX``).
        hidden_files: Files added for grading only: repo path -> content.
        solution_files: Reference change: repo path -> content.
    """

    id: str
    repo: str
    repo_dir: Path
    title: str
    body: str
    expect_context: tuple[str, ...] = ()
    expect_changed: tuple[str, ...] = ()
    expect_unchanged: tuple[str, ...] = ()
    contains: tuple[FilePattern, ...] = ()
    hidden_checks: tuple[CheckSpec, ...] = ()
    hidden_files: dict[str, str] = field(default_factory=dict)
    solution_files: dict[str, str] = field(default_factory=dict)

    @property
    def ticket(self) -> Ticket:
        """The case as a ticket, as the agents see it."""
        return Ticket(key=self.id, title=self.title, body=self.body.strip(), url=f"eval://{self.id}",
                      labels=["autoforge"])


def load_suite(root: Path, case_ids: list[str] | None = None) -> list[EvalCase]:
    """Load and validate the cases of the suite in ``root``, sorted by ID.

    Args:
        root: Suite folder (holds ``repos/`` and ``cases/``).
        case_ids: Only these cases (all if ``None`` or empty).

    Raises:
        SuiteError: If the suite, a case, or a requested case ID is invalid.
    """
    cases_dir = root / "cases"
    if not cases_dir.is_dir():
        raise SuiteError(f"no cases folder in suite: {cases_dir}")
    available = sorted(p.name for p in cases_dir.iterdir() if (p / "case.toml").is_file())
    wanted = case_ids or available
    unknown = sorted(set(wanted) - set(available))
    if unknown:
        raise SuiteError(f"unknown case(s): {', '.join(unknown)}; available: {', '.join(available)}")
    return [load_case(root, case_id) for case_id in sorted(set(wanted))]


def load_case(root: Path, case_id: str) -> EvalCase:
    """Load and validate one case.

    Raises:
        SuiteError: If the case is malformed or refers to files that do not exist.
    """
    if not _CASE_ID.fullmatch(case_id):
        raise SuiteError(f"invalid case id {case_id!r} (lower-case letters, digits and '-')")
    folder = root / "cases" / case_id
    try:
        data = tomllib.loads((folder / "case.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SuiteError(f"{case_id}: cannot read case.toml: {exc}") from exc
    where = f"{case_id}/case.toml"
    _no_unknown_keys(data, _CASE_KEYS, where)
    expect = data.get("expect", {})
    if not isinstance(expect, dict):
        raise SuiteError(f"{where}: [expect] must be a table")
    _no_unknown_keys(expect, _EXPECT_KEYS, f"{where} [expect]")

    repo = _string(data, "repo", where)
    repo_dir = root / "repos" / repo
    if not repo_dir.is_dir():
        raise SuiteError(f"{where}: fixture repository {repo!r} not found in {root / 'repos'}")
    repo_paths = set(read_tree(repo_dir))
    solution = read_tree(folder / "solution")
    hidden = read_tree(folder / "hidden")

    case = EvalCase(
        id=case_id, repo=repo, repo_dir=repo_dir,
        title=_string(data, "title", where), body=_string(data, "body", where),
        expect_context=_paths(expect, "context", where), expect_changed=_paths(expect, "changed", where),
        expect_unchanged=_paths(expect, "unchanged", where), contains=_patterns(expect, where),
        hidden_checks=_hidden_checks(data.get("hidden_checks", []), where),
        hidden_files=hidden, solution_files=solution,
    )
    after_solution = repo_paths | set(solution)
    for label, paths, universe in (("context", case.expect_context, repo_paths),
                                   ("unchanged", case.expect_unchanged, repo_paths),
                                   ("changed", case.expect_changed, after_solution),
                                   ("contains", tuple(p.path for p in case.contains), after_solution)):
        missing = [p for p in paths if p not in universe]
        if missing:
            raise SuiteError(f"{where}: expect.{label} lists files that do not exist: {', '.join(missing)}")
    overlap = set(case.expect_changed) & set(case.expect_unchanged)
    if overlap:
        raise SuiteError(f"{where}: files both changed and unchanged: {', '.join(sorted(overlap))}")
    if case.hidden_checks and not hidden:
        raise SuiteError(f"{where}: hidden_checks need files in {case_id}/hidden/")
    return case


def read_tree(folder: Path) -> dict[str, str]:
    """Read every file under ``folder`` as text: repo-relative POSIX path -> content (empty if no folder).

    Tool caches (``__pycache__``, ``.ruff_cache`` ...) are skipped.

    Raises:
        SuiteError: If a file is not UTF-8 text or is a symlink.
    """
    if not folder.is_dir():
        return {}
    files: dict[str, str] = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise SuiteError(f"symlinks are not allowed in the suite: {path}")
        if not path.is_file() or _SKIP_DIRS.intersection(path.relative_to(folder).parts):
            continue
        rel = PurePosixPath(*path.relative_to(folder).parts).as_posix()
        try:
            files[rel] = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SuiteError(f"not UTF-8 text: {path}") from exc
    return files


def _no_unknown_keys(table: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise SuiteError(f"{where}: unknown keys: {', '.join(sorted(unknown))}")


def _string(table: dict[str, Any], key: str, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SuiteError(f"{where}: '{key}' must be a non-empty string")
    return value


def _paths(table: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise SuiteError(f"{where}: expect.{key} must be a list of paths")
    return tuple(value)


def _patterns(expect: dict[str, Any], where: str) -> tuple[FilePattern, ...]:
    raw = expect.get("contains", [])
    if not isinstance(raw, list):
        raise SuiteError(f"{where}: expect.contains must be a list of {{ path, pattern }} tables")
    patterns = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"path", "pattern"}:
            raise SuiteError(f"{where}: each expect.contains entry needs exactly 'path' and 'pattern'")
        path, pattern = _string(item, "path", where), _string(item, "pattern", where)
        try:
            re.compile(pattern)
        except re.error as exc:
            raise SuiteError(f"{where}: invalid pattern {pattern!r}: {exc}") from exc
        patterns.append(FilePattern(path, pattern))
    return tuple(patterns)


def _hidden_checks(raw: Any, where: str) -> tuple[CheckSpec, ...]:
    if not isinstance(raw, list):
        raise SuiteError(f"{where}: hidden_checks must be an array of tables ([[hidden_checks]])")
    checks = []
    for item in raw:
        if not isinstance(item, dict):
            raise SuiteError(f"{where}: each [[hidden_checks]] must be a table")
        _no_unknown_keys(item, {"name", "run", "timeout"}, f"{where} [[hidden_checks]]")
        timeout = item.get("timeout", 600)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 0 < timeout <= MAX_TIMEOUT:
            raise SuiteError(f"{where}: hidden check timeout must be 1-{MAX_TIMEOUT} seconds")
        checks.append(CheckSpec(HIDDEN_CHECK_PREFIX + _string(item, "name", where), _string(item, "run", where),
                                timeout))
    if len({c.name for c in checks}) != len(checks):
        raise SuiteError(f"{where}: hidden check names must be unique")
    return tuple(checks)
