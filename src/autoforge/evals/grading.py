"""Grade a run: did the agents see the right files, and is the change correct?

Two scores, both deterministic (no LLM judges):

- **Context** (``score_context``): recall of the files the case expects in
  the context, plus how much context was sent.
- **Change** (``grade_change``):

  - *policy*: the change passes ``domain.policy`` (what the graph enforces);
  - *scope*: it creates/modifies the expected files, leaves the protected ones
    alone, and the changed repository matches the expected patterns;
  - *checks*: the repository's own checks and the case's **hidden acceptance
    checks** pass in the sandbox on the change plus the hidden files.

A change is **resolved** when policy and every check pass. That is the
headline metric; the scope graders explain *why* a change failed or drifted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from autoforge.domain.checks import CHECKS_CONFIG, checks_for
from autoforge.domain.errors import ChangeValidationError, VerificationError
from autoforge.domain.models import CheckSpec, CodeChange, FileChange, RepoSnapshot
from autoforge.domain.policy import validate_change
from autoforge.evals.suite import HIDDEN_CHECK_PREFIX, EvalCase
from autoforge.ports import Verifier

OUTPUT_TAIL = 1500
"""Characters of a failing check's output kept in the result."""


@dataclass(frozen=True)
class ContextScore:
    """How well the context matched the case.

    Attributes:
        missing: Expected files that were not shown.
        files: Number of files shown in full.
        excerpts: Number of files shown as excerpts.
        bytes: Total size of the context (full files and excerpts).
        recall: Share of expected files shown (1.0 if the case expects none).
    """

    missing: tuple[str, ...]
    files: int
    excerpts: int
    bytes: int
    recall: float


@dataclass(frozen=True)
class CheckOutcome:
    """Result of one check on the graded change.

    Attributes:
        name: Check name (hidden checks start with ``HIDDEN_CHECK_PREFIX``).
        status: ``passed`` / ``failed`` / ``timeout`` / ``error``.
        output: End of the output if the check did not pass.
    """

    name: str
    status: str
    output: str = ""

    @property
    def passed(self) -> bool:
        """True if the check passed."""
        return self.status == "passed"

    @property
    def hidden(self) -> bool:
        """True for a hidden acceptance check."""
        return self.name.startswith(HIDDEN_CHECK_PREFIX)


@dataclass(frozen=True)
class ChangeScore:
    """Grades of one change.

    Attributes:
        policy_error: Why ``domain.policy`` rejected the change; empty if it passed.
        changed_missing: Expected files the change did not create or modify.
        unchanged_violations: Protected files the change touched.
        patterns_missing: ``path: pattern`` entries that did not match after the change.
        checks: Repository and hidden checks, in run order; empty if they did not run.
        checks_skipped: Why the checks did not run (no sandbox, policy violation, ...); empty if they ran.
    """

    policy_error: str
    changed_missing: tuple[str, ...]
    unchanged_violations: tuple[str, ...]
    patterns_missing: tuple[str, ...]
    checks: tuple[CheckOutcome, ...]
    checks_skipped: str

    @property
    def scope_ok(self) -> bool:
        """True if the change touched what it should, left the rest alone, and matched every pattern."""
        return not (self.changed_missing or self.unchanged_violations or self.patterns_missing)

    @property
    def hidden_passed(self) -> int:
        """Number of hidden checks that passed."""
        return sum(1 for c in self.checks if c.hidden and c.passed)

    @property
    def hidden_total(self) -> int:
        """Number of hidden checks that ran."""
        return sum(1 for c in self.checks if c.hidden)

    @property
    def resolved(self) -> bool:
        """True if the change passes policy and every check (repository and hidden) ran and passed."""
        return (not self.policy_error and not self.checks_skipped and bool(self.checks)
                and all(c.passed for c in self.checks))


def score_context(case: EvalCase, snapshot: RepoSnapshot) -> ContextScore:
    """Compare the snapshot's context with the files the case expects."""
    shown = set(snapshot.files) | set(snapshot.excerpts)
    missing = tuple(p for p in case.expect_context if p not in shown)
    expected = len(case.expect_context)
    size = sum(len(c.encode()) for c in (*snapshot.files.values(), *snapshot.excerpts.values()))
    return ContextScore(missing=missing, files=len(snapshot.files), excerpts=len(snapshot.excerpts), bytes=size,
                        recall=(expected - len(missing)) / expected if expected else 1.0)


def repo_checks(files: dict[str, str]) -> list[CheckSpec]:
    """The fixture repository's own checks (``.autoforge.toml`` or detection), as production chooses them."""
    return checks_for(files, config=files.get(CHECKS_CONFIG), pyproject=files.get("pyproject.toml"))


def grade_change(case: EvalCase, snapshot: RepoSnapshot, change: CodeChange, base_files: dict[str, str], *,
                 verifier: Verifier | None, archive: bytes | None) -> ChangeScore:
    """Grade ``change`` for ``case``.

    Args:
        case: The case.
        snapshot: The context the agents saw (``domain.policy`` needs it).
        change: The change to grade.
        base_files: Every file of the fixture before the change.
        verifier: Sandbox to run the checks in; ``None`` skips them (the change cannot count as resolved).
        archive: The fixture as a tarball (required with ``verifier``).
    """
    policy_error = ""
    try:
        validate_change(change, snapshot)
    except ChangeValidationError as exc:
        policy_error = str(exc)

    written = {f.path for f in change.files if f.action in ("create", "modify")}
    touched = {f.path for f in change.files}
    after = apply_change(base_files, change)
    patterns_missing = tuple(f"{p.path}: {p.pattern}" for p in case.contains
                             if not re.search(p.pattern, after.get(p.path, "")))

    checks: tuple[CheckOutcome, ...] = ()
    if policy_error:
        skipped = "change violates the policy"
    elif verifier is None or archive is None:
        skipped = "no verification sandbox (SANDBOX_URL)"
    else:
        checks, skipped = run_checks(case, change, base_files, verifier, archive)
    return ChangeScore(
        policy_error=policy_error,
        changed_missing=tuple(p for p in case.expect_changed if p not in written),
        unchanged_violations=tuple(p for p in case.expect_unchanged if p in touched),
        patterns_missing=patterns_missing, checks=checks, checks_skipped=skipped,
    )


def run_checks(case: EvalCase, change: CodeChange, base_files: dict[str, str], verifier: Verifier,
               archive: bytes) -> tuple[tuple[CheckOutcome, ...], str]:
    """Run the repository's checks and the hidden checks on ``change`` plus the hidden files.

    Returns:
        The outcomes, and an empty string; or no outcomes and why the sandbox could not run them.
    """
    hidden = [FileChange(path=p, action="create", content=c) for p, c in case.hidden_files.items()]
    files = [f for f in change.files if f.path not in case.hidden_files] + hidden
    graded = change.model_copy(update={"files": files})
    checks = [*repo_checks(base_files), *case.hidden_checks]
    try:
        report = verifier.verify(archive, graded, checks)
    except VerificationError as exc:
        return (), f"sandbox error: {exc}"
    return tuple(CheckOutcome(c.name, c.status, "" if c.passed else c.output[-OUTPUT_TAIL:])
                 for c in report.checks), ""


def apply_change(files: dict[str, str], change: CodeChange) -> dict[str, str]:
    """The repository content after ``change``: path -> content."""
    after = dict(files)
    for f in change.files:
        if f.action == "delete":
            after.pop(f.path, None)
        else:
            after[f.path] = f.content
    return after


def solution_change(case: EvalCase, base_files: dict[str, str]) -> CodeChange:
    """The case's reference solution as a ``CodeChange`` (create or modify per file)."""
    files = [FileChange(path=p, action="modify" if p in base_files else "create", content=c)
             for p, c in case.solution_files.items()]
    return CodeChange(summary="Reference solution", commit_message="Reference solution",
                      pr_title="Reference solution", pr_body="", files=files)
