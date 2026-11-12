"""Change policy: rules every agent-produced change must satisfy before it touches a repo.

This module is the code-level enforcement of the hard constraints in
CLAUDE.md. Whatever the LLM outputs (including output steered by a malicious
ticket), these checks decide what may reach GitHub:

- paths must stay inside the repository and be well-formed;
- ``.git/`` and ``.github/workflows/`` (CI/CD) may never be changed;
- a file may only be modified if the agent actually saw its current content;
- writes may only go to agent-owned branches, never the default branch.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from autoforge.domain.errors import ChangeValidationError, SafetyError
from autoforge.domain.models import BaseRef, CodeChange, FileChange, RepoSnapshot

MAX_FILES_PER_CHANGE = 50
"""Upper bound on files in one change; larger changes are rejected as suspicious."""

FORBIDDEN_PREFIXES = (".git/", ".github/workflows/")
"""Path prefixes the agent may never write to (git internals and CI pipelines)."""


def validate_change(change: CodeChange, snapshot: RepoSnapshot) -> None:
    """Check a proposed change against every policy rule.

    Args:
        change: The change returned by the coder agent.
        snapshot: The repository state the agent was shown.

    Raises:
        ChangeValidationError: On the first rule that is violated. The message
            says which file and which rule, and is posted back on the ticket.
    """
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
    """Guarantee that agents only ever write to their own branches.

    Called right before any commit is pushed. This is the "PR-only, never touch
    the default branch" rule expressed in code.

    Args:
        branch: Branch the agent wants to write to, e.g. ``"autoforge/issue-7"``.
        base: The base commit; its ``branch`` is the protected default branch.
        required_prefix: Prefix every agent branch must start with, e.g. ``"autoforge/"``.

    Raises:
        SafetyError: If ``branch`` is the default branch or lacks the agent prefix.
    """
    if branch == base.branch:
        raise SafetyError("Refusing to write to the default branch")
    if not branch.startswith(required_prefix):
        raise SafetyError(f"Refusing to write to non-agent branch {branch!r}")


def _validate_path(path: str) -> None:
    """Reject malformed, absolute, escaping, or forbidden paths.

    Accepts only normalised repository-relative POSIX paths such as
    ``"bms/protection.py"``. Rejects e.g. ``"/etc/passwd"``, ``"../x.py"``,
    ``"./a.py"``, ``"a\\b.py"``, ``"src//a.py"`` and anything under
    ``.git/`` or ``.github/workflows/``.

    Raises:
        ChangeValidationError: If the path is not acceptable.
    """
    p = PurePosixPath(path)
    if not path or path != p.as_posix() or p.is_absolute() or "\\" in path or ".." in p.parts or path.startswith("./"):
        raise ChangeValidationError(f"Invalid path: {path!r}")
    if path == ".git" or any(path.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        raise ChangeValidationError(f"Path is not allowed to be changed by the agent: {path}")


def _validate_action(f: FileChange, snapshot: RepoSnapshot) -> None:
    """Check that the requested action makes sense for the file's current state.

    - ``create``: the file must not already exist.
    - ``modify``: the agent must have been shown the file's full content
      (prevents blind overwrites of code it never read).
    - ``delete``: the file must exist.
    - create/modify content must not be empty.

    Raises:
        ChangeValidationError: If the action is inconsistent with the repo.
    """
    exists = f.path in snapshot.modes
    if f.action == "create" and exists:
        raise ChangeValidationError(f"Cannot create {f.path}: file already exists")
    if f.action == "modify" and f.path not in snapshot.files:
        raise ChangeValidationError(f"Cannot modify {f.path}: its current content was not provided to the agent")
    if f.action == "delete" and not exists:
        raise ChangeValidationError(f"Cannot delete {f.path}: file does not exist")
    if f.action != "delete" and not f.content.strip():
        raise ChangeValidationError(f"Empty content for {f.action} of {f.path}")
