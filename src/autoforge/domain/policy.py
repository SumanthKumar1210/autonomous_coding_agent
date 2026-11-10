"""Change policy: rules every agent-produced change must satisfy before it touches a repo.

These are the code-level enforcement of the hard constraints in CLAUDE.md.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from autoforge.domain.errors import ChangeValidationError, SafetyError
from autoforge.domain.models import BaseRef, CodeChange, FileChange, RepoSnapshot

MAX_FILES_PER_CHANGE = 50
FORBIDDEN_PREFIXES = (".git/", ".github/workflows/")


def validate_change(change: CodeChange, snapshot: RepoSnapshot) -> None:
    if not change.files:
        raise ChangeValidationError("Agent produced no file changes")
    if len(change.files) > MAX_FILES_PER_CHANGE:
        raise ChangeValidationError(f"Change touches {len(change.files)} files (limit {MAX_FILES_PER_CHANGE})")
    seen: set[str] = set()
    for f in change.files:
        _validate_path(f.path)
        if f.path in seen:
            raise ChangeValidationError(f"Duplicate path in change: {f.path}")
        seen.add(f.path)
        _validate_action(f, snapshot)


def assert_writable_branch(branch: str, base: BaseRef, *, required_prefix: str) -> None:
    """Agents may only write to their own branches, never to the default branch."""
    if branch == base.branch:
        raise SafetyError("Refusing to write to the default branch")
    if not branch.startswith(required_prefix):
        raise SafetyError(f"Refusing to write to non-agent branch {branch!r}")


def _validate_path(path: str) -> None:
    p = PurePosixPath(path)
    if not path or path != p.as_posix() or p.is_absolute() or "\\" in path or ".." in p.parts or path.startswith("./"):
        raise ChangeValidationError(f"Invalid path: {path!r}")
    if path == ".git" or any(path.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        raise ChangeValidationError(f"Path is not allowed to be changed by the agent: {path}")


def _validate_action(f: FileChange, snapshot: RepoSnapshot) -> None:
    exists = f.path in snapshot.modes
    if f.action == "create" and exists:
        raise ChangeValidationError(f"Cannot create {f.path}: file already exists")
    if f.action == "modify" and f.path not in snapshot.files:
        raise ChangeValidationError(f"Cannot modify {f.path}: its current content was not provided to the agent")
    if f.action == "delete" and not exists:
        raise ChangeValidationError(f"Cannot delete {f.path}: file does not exist")
    if f.action != "delete" and not f.content.strip():
        raise ChangeValidationError(f"Empty content for {f.action} of {f.path}")
