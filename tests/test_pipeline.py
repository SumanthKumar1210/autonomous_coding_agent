"""End-to-end tests of a ticket run: real workflow graph + real GitHub adapters, against fakes.

GitHub is ``FakeGitHub`` (HTTP-level fake); the agents are ``FakeCoder`` /
``FakeReviewer`` with scripted answers.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from autoforge.domain.errors import TicketError
from autoforge.domain.models import FileChange
from autoforge.integrations.github.issues import GitHubIssuesSource
from autoforge.integrations.github.repo import GitHubRepoHost
from autoforge.workflow.graph import checkpoint_serializer
from autoforge.workflow.pipeline import Dependencies, RunOutcome, WorkflowConfig, resume_run, run_ticket

from tests.fake_agents import GOOD_FILES, FakeCoder, FakeReviewer, approve, reject
from tests.fake_github import FakeGitHub

EVIL = [FileChange(path=".github/workflows/ci.yml", action="create", content="on: push")]


def _deps(fake: FakeGitHub, coder: FakeCoder, reviewer: FakeReviewer | None = None,
          checkpointer: SqliteSaver | None = None) -> Dependencies:
    """Wire the real GitHub adapters (on the fake) with the given agents."""
    gh = fake.client()
    extra = {"checkpointer": checkpointer} if checkpointer else {}
    return Dependencies(tickets=GitHubIssuesSource(gh), repo=GitHubRepoHost(gh, branch_prefix="autoforge/"),
                        coder=coder, reviewer=reviewer or FakeReviewer(), **extra)


def _run(fake: FakeGitHub, coder: FakeCoder, reviewer: FakeReviewer | None = None, key: str = "7",
         config: WorkflowConfig | None = None):
    """Run the real workflow for ticket ``key`` against the fakes."""
    return run_ticket(key, _deps(fake, coder, reviewer), config or WorkflowConfig())


def _sqlite(path: Path) -> SqliteSaver:
    """A SQLite checkpointer on ``path``, as production uses (fresh connection = fresh "process")."""
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False), serde=checkpoint_serializer())


# ------------------------------------------------------------------ happy path & idempotency


def test_happy_path_opens_pr_and_reports_back() -> None:
    """An approved change opens a PR on the agent branch, leaves main untouched and reports back on the issue."""
    fake = FakeGitHub()
    fake.add_issue(7, title="Add C to F conversion")
    coder = FakeCoder()

    result = _run(fake, coder, FakeReviewer(approve("Consider a docstring.")))

    assert result.outcome is RunOutcome.PR_OPENED
    assert result.pr_url == fake.pulls[0]["html_url"]
    pr = fake.pulls[0]
    assert pr["head"] == "autoforge/issue-7" and pr["base"] == "main"
    assert "Closes #7" in pr["body"] and result.run_id in pr["body"]
    assert "Approved by the AutoForge reviewer after 1 coder attempt(s)" in pr["body"]
    assert "Consider a docstring." in pr["body"]  # non-blocking notes are surfaced to the human
    assert "tokens in/out 140/60" in pr["body"]  # coder + reviewer usage summed
    assert fake.refs["autoforge/issue-7"] == "commit-1"
    assert fake.refs["main"] == "commit-base"  # default branch untouched
    assert fake.created_commits[0]["parents"] == ["commit-base"]
    assert fake.created_trees[0]["tree"][0]["content"].endswith("\n")
    assert fake.labels[7] == ["autoforge:pr-opened"]
    assert result.pr_url in fake.comments[7][-1]
    ticket, snapshot = coder.seen
    assert ticket.title == "Add C to F conversion"
    assert snapshot.files["src/app.py"] == "print('hi')\n"


def test_existing_open_pr_is_not_duplicated() -> None:
    """Running the same ticket twice does not open a second PR (idempotency)."""
    fake = FakeGitHub()
    fake.add_issue(7)
    _run(fake, FakeCoder())

    second = _run(fake, FakeCoder())

    assert second.outcome is RunOutcome.ALREADY_OPEN
    assert len(fake.pulls) == 1


def test_stale_branch_without_pr_is_reset() -> None:
    """A leftover agent branch without an open PR is reset to the new commit."""
    fake = FakeGitHub()
    fake.add_issue(7)
    fake.refs["autoforge/issue-7"] = "old-commit"

    result = _run(fake, FakeCoder())

    assert result.outcome is RunOutcome.PR_OPENED
    assert fake.refs["autoforge/issue-7"] == "commit-1"
    assert ("PATCH", "/git/refs/heads/autoforge/issue-7") in fake.calls


# ------------------------------------------------------------------ coder <-> reviewer loop


def test_rejected_change_is_revised_with_findings() -> None:
    """A rejected change goes back to the coder with the findings; the revised change is published."""
    fake = FakeGitHub()
    fake.add_issue(7)
    coder = FakeCoder()
    reviewer = FakeReviewer(reject("Reject non-numeric input."), approve())

    result = _run(fake, coder, reviewer)

    assert result.outcome is RunOutcome.PR_OPENED
    assert len(coder.calls) == 2 and len(reviewer.reviewed) == 2
    assert coder.calls[0][2] is None  # first attempt has no feedback
    revision = coder.calls[1][2]
    assert revision is not None and "[blocker] src/temperature.py: Reject non-numeric input." in revision.feedback
    assert revision.previous.files == GOOD_FILES
    assert "after 2 coder attempt(s)" in fake.pulls[0]["body"]


def test_reviewer_approval_with_major_finding_does_not_pass() -> None:
    """approved=True is not trusted while a major finding is listed: the change is revised."""
    fake = FakeGitHub()
    fake.add_issue(7)
    coder = FakeCoder()
    contradictory = approve().model_copy(update={"findings": reject(severity="major").findings})

    result = _run(fake, coder, FakeReviewer(contradictory, approve()))

    assert result.outcome is RunOutcome.PR_OPENED
    assert len(coder.calls) == 2


def test_review_rejected_after_max_attempts_fails_without_pr() -> None:
    """If the reviewer never approves, the run fails after max_attempts and posts the findings; no PR."""
    fake = FakeGitHub()
    fake.add_issue(7)
    coder = FakeCoder()

    result = _run(fake, coder, FakeReviewer(reject("Off-by-one in limit check.")), config=WorkflowConfig(max_attempts=2))

    assert result.outcome is RunOutcome.FAILED
    assert "ReviewRejectedError" in (result.error or "")
    assert len(coder.calls) == 2
    assert not fake.pulls and not fake.created_trees and "autoforge/issue-7" not in fake.refs
    assert fake.labels[7] == ["autoforge:failed"]
    assert "Off-by-one in limit check." in fake.comments[7][-1]
    assert "after 2 attempt(s)" in fake.comments[7][-1]


def test_policy_violation_is_fed_back_to_coder() -> None:
    """A policy violation is not reviewed; the coder gets the violation as feedback and can fix it."""
    fake = FakeGitHub()
    fake.add_issue(7)
    coder = FakeCoder(attempts=[EVIL, GOOD_FILES])
    reviewer = FakeReviewer()

    result = _run(fake, coder, reviewer)

    assert result.outcome is RunOutcome.PR_OPENED
    assert len(reviewer.reviewed) == 1 and reviewer.reviewed[0].files == GOOD_FILES
    revision = coder.calls[1][2]
    assert revision is not None and "Change policy violation" in revision.feedback


def test_invalid_change_never_reaches_github() -> None:
    """A change that keeps violating policy fails after max_attempts, before any write to GitHub."""
    fake = FakeGitHub()
    fake.add_issue(7)

    result = _run(fake, FakeCoder(files=EVIL))

    assert result.outcome is RunOutcome.FAILED
    assert "ChangeValidationError" in (result.error or "")
    assert not fake.created_trees and not fake.pulls
    assert fake.labels[7] == ["autoforge:failed"]


# ------------------------------------------------------------------ human-in-the-loop approval


def test_approval_gate_pauses_and_resume_opens_pr(tmp_path: Path) -> None:
    """With require_approval, the run pauses before any write; resuming in a new "process" opens the PR."""
    fake = FakeGitHub()
    fake.add_issue(7)
    config = WorkflowConfig(require_approval=True)
    db = tmp_path / "checkpoints.sqlite"

    paused = run_ticket("7", _deps(fake, FakeCoder(), checkpointer=_sqlite(db)), config)

    assert paused.outcome is RunOutcome.AWAITING_APPROVAL
    assert not fake.created_trees and not fake.pulls
    assert fake.labels[7] == ["autoforge:awaiting-approval"]
    assert f"autoforge resume {paused.run_id}" in fake.comments[7][-1]

    coder = FakeCoder()  # a fresh process: new agents, new DB connection
    resumed = resume_run(paused.run_id, True, _deps(fake, coder, checkpointer=_sqlite(db)), config)

    assert resumed.outcome is RunOutcome.PR_OPENED and resumed.run_id == paused.run_id
    assert not coder.calls  # the approved change is published as reviewed, not regenerated
    assert paused.run_id in fake.pulls[0]["body"]
    assert fake.labels[7] == ["autoforge:pr-opened"]


def test_approval_gate_reject_opens_no_pr(tmp_path: Path) -> None:
    """Rejecting at the approval gate publishes nothing and reports it on the issue."""
    fake = FakeGitHub()
    fake.add_issue(7)
    config = WorkflowConfig(require_approval=True)
    db = tmp_path / "checkpoints.sqlite"
    paused = run_ticket("7", _deps(fake, FakeCoder(), checkpointer=_sqlite(db)), config)

    rejected = resume_run(paused.run_id, False, _deps(fake, FakeCoder(), checkpointer=_sqlite(db)), config)

    assert rejected.outcome is RunOutcome.REJECTED
    assert not fake.created_trees and not fake.pulls
    assert fake.labels[7] == ["autoforge:failed"]
    assert "rejected at the approval step" in fake.comments[7][-1]


def test_resume_unknown_or_finished_run_fails_cleanly(tmp_path: Path) -> None:
    """Resuming a run that does not exist, or is not paused, fails without side effects."""
    fake = FakeGitHub()
    fake.add_issue(7)
    db = tmp_path / "checkpoints.sqlite"
    done = run_ticket("7", _deps(fake, FakeCoder(), checkpointer=_sqlite(db)), WorkflowConfig())

    unknown = resume_run("nope", True, _deps(fake, FakeCoder(), checkpointer=_sqlite(db)), WorkflowConfig())
    finished = resume_run(done.run_id, True, _deps(fake, FakeCoder(), checkpointer=_sqlite(db)), WorkflowConfig())

    assert unknown.outcome is RunOutcome.FAILED and "Unknown run id" in (unknown.error or "")
    assert finished.outcome is RunOutcome.FAILED and "not awaiting approval" in (finished.error or "")
    assert len(fake.pulls) == 1


# ------------------------------------------------------------------ failures


def test_coder_failure_is_reported_on_ticket() -> None:
    """If the coder fails, the issue gets a 'failed' label and an error comment, and nothing is pushed."""
    fake = FakeGitHub()
    fake.add_issue(7)

    result = _run(fake, FakeCoder(error=RuntimeError("model unavailable")))

    assert result.outcome is RunOutcome.FAILED
    assert "model unavailable" in (result.error or "")
    assert fake.labels[7] == ["autoforge:failed"]
    assert "model unavailable" in fake.comments[7][-1]
    assert not fake.pulls and "autoforge/issue-7" not in fake.refs


def test_reviewer_failure_is_reported_on_ticket() -> None:
    """If the reviewer fails, the run fails and nothing is pushed."""
    fake = FakeGitHub()
    fake.add_issue(7)

    result = _run(fake, FakeCoder(), FakeReviewer(error=RuntimeError("reviewer down")))

    assert result.outcome is RunOutcome.FAILED
    assert "reviewer down" in fake.comments[7][-1]
    assert not fake.pulls and not fake.created_trees


def test_pull_request_number_is_rejected_cleanly() -> None:
    """Passing a PR number instead of an issue fails cleanly without touching it."""
    fake = FakeGitHub()
    fake.add_issue(7, pull_request={"url": "x"})

    result = _run(fake, FakeCoder())

    assert result.outcome is RunOutcome.FAILED
    assert TicketError.__name__ in (result.error or "") and "pull request" in (result.error or "")
    assert fake.labels[7] == [] and fake.comments[7] == []


def test_empty_repository_is_reported_clearly() -> None:
    """An empty repo (GitHub 409) fails with a clear 'push an initial commit' message."""
    fake = FakeGitHub()
    fake.add_issue(7)
    fake.fail[("GET", r"/git/ref/heads/main")] = 409

    result = _run(fake, FakeCoder())

    assert result.outcome is RunOutcome.FAILED
    assert "push an initial commit" in (result.error or "")
    assert "push an initial commit" in fake.comments[7][-1]


def test_missing_issue_fails_cleanly() -> None:
    """A non-existent issue number returns FAILED instead of crashing."""
    result = _run(FakeGitHub(), FakeCoder(), key="404")

    assert result.outcome is RunOutcome.FAILED
    assert "404" in (result.error or "")
