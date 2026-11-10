from __future__ import annotations

import pytest

from autoforge.domain.errors import ChangeValidationError, SafetyError
from autoforge.domain.models import BaseRef, CodeChange, FileChange, RepoSnapshot
from autoforge.domain.policy import assert_writable_branch, validate_change

BASE = BaseRef(branch="main", commit_sha="c0", tree_sha="t0")
SNAPSHOT = RepoSnapshot(
    base=BASE,
    modes={"README.md": "100644", "src/app.py": "100644", "big/data.bin": "100644"},
    files={"README.md": "# x\n", "src/app.py": "print(1)\n"},
)


def _change(*files: FileChange) -> CodeChange:
    return CodeChange(summary="s", commit_message="m", pr_title="t", pr_body="b", files=list(files))


def _f(path: str, action: str = "create", content: str = "x = 1") -> FileChange:
    return FileChange(path=path, action=action, content=content)  # type: ignore[arg-type]


def test_valid_change_passes() -> None:
    validate_change(_change(_f("src/new.py"), _f("src/app.py", "modify"), _f("big/data.bin", "delete", "")), SNAPSHOT)


@pytest.mark.parametrize("path", ["/etc/passwd", "../x.py", "src/../../x.py", "./a.py", "a\\b.py", "",
                                  ".git/config", ".github/workflows/ci.yml", "src//a.py"])
def test_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ChangeValidationError):
        validate_change(_change(_f(path)), SNAPSHOT)


@pytest.mark.parametrize("change", [
    _change(),
    _change(_f("README.md", "create")),            # already exists
    _change(_f("big/data.bin", "modify")),         # content never shown to the model
    _change(_f("nope.py", "delete", "")),          # does not exist
    _change(_f("a.py"), _f("a.py")),               # duplicate
    _change(_f("a.py", content="   ")),            # empty
])
def test_rejects_invalid_actions(change: CodeChange) -> None:
    with pytest.raises(ChangeValidationError):
        validate_change(change, SNAPSHOT)


@pytest.mark.parametrize("branch", ["main", "feature/x", "release"])
def test_only_agent_branches_are_writable(branch: str) -> None:
    with pytest.raises(SafetyError):
        assert_writable_branch(branch, BASE, required_prefix="autoforge/")


def test_agent_branch_is_writable() -> None:
    assert_writable_branch("autoforge/issue-1", BASE, required_prefix="autoforge/")
