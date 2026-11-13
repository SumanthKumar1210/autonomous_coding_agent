"""Tests for workflow.worker: authorization, run/approve dispatch, and the paused-run rules."""

from __future__ import annotations

from autoforge.domain.models import JobStatus, Trigger, TriggerKind
from autoforge.integrations.github.issues import GitHubIssuesSource
from autoforge.integrations.github.repo import GitHubRepoHost
from autoforge.integrations.sqlite.job_store import SqliteJobStore
from autoforge.workflow.pipeline import Dependencies, WorkflowConfig
from autoforge.workflow.worker import SKIPPED, UNAUTHORIZED, Worker, process_job
from tests.fake_agents import FakeCoder, FakeReviewer
from tests.fake_github import FakeGitHub

APPROVAL = WorkflowConfig(require_approval=True)


def setup(config: WorkflowConfig | None = None) -> tuple[FakeGitHub, SqliteJobStore, Worker, FakeCoder]:
    """A fake GitHub with open issue #7, an in-memory job store, and a worker wired to fakes."""
    config = config or WorkflowConfig()
    fake = FakeGitHub()
    fake.add_issue(7)
    gh = fake.client()
    coder = FakeCoder()
    deps = Dependencies(tickets=GitHubIssuesSource(gh), repo=GitHubRepoHost(gh, branch_prefix="autoforge/"),
                        coder=coder, reviewer=FakeReviewer())
    store = SqliteJobStore(":memory:")
    return fake, store, Worker(store, deps, config), coder


def enqueue(store: SqliteJobStore, kind: TriggerKind, actor: str | None = "maintainer", delivery: str = "d-1") -> int:
    """Queue a trigger for issue #7 and return the job ID."""
    job = store.add(Trigger(kind, "7", delivery, actor))
    assert job is not None
    return job.id


def test_labelled_issue_from_maintainer_opens_pr() -> None:
    """A queued RUN from a user with write access runs the workflow and records the result."""
    fake, store, worker, _ = setup()
    job_id = enqueue(store, TriggerKind.RUN)

    assert worker.run_pending() == 1

    job = store.get(job_id)
    assert job is not None and job.status is JobStatus.DONE and job.outcome == "pr_opened" and job.run_id
    assert len(fake.pulls) == 1


def test_actor_without_write_access_is_ignored() -> None:
    """A label added by a read-only (or unknown) user does nothing at all on GitHub."""
    fake, store, worker, coder = setup()
    job_ids = [enqueue(store, TriggerKind.RUN, "visitor", "d-1"), enqueue(store, TriggerKind.RUN, "stranger", "d-2")]

    worker.run_pending()

    assert [store.get(j).outcome for j in job_ids] == [UNAUTHORIZED, UNAUTHORIZED]  # type: ignore[union-attr]
    assert not coder.calls and not fake.pulls and fake.comments[7] == [] and fake.labels[7] == []


def test_comment_approval_resumes_paused_run() -> None:
    """With the approval gate, a run pauses; an /autoforge approve job publishes the reviewed change."""
    fake, store, worker, coder = setup(APPROVAL)
    run_job = enqueue(store, TriggerKind.RUN, delivery="d-1")
    worker.run_pending()
    paused = store.get(run_job)
    assert paused is not None and paused.outcome == "awaiting_approval" and not fake.pulls
    assert "/autoforge approve" in fake.comments[7][-1]

    approve_job = enqueue(store, TriggerKind.APPROVE, delivery="d-2")
    worker.run_pending()

    approved = store.get(approve_job)
    assert approved is not None and approved.outcome == "pr_opened" and approved.run_id == paused.run_id
    assert len(fake.pulls) == 1 and len(coder.calls) == 1  # published as reviewed, not regenerated


def test_comment_reject_discards_paused_run() -> None:
    """An /autoforge reject job ends the paused run without a PR."""
    fake, store, worker, _ = setup(APPROVAL)
    enqueue(store, TriggerKind.RUN, delivery="d-1")
    worker.run_pending()

    reject_job = enqueue(store, TriggerKind.REJECT, delivery="d-2")
    worker.run_pending()

    assert store.get(reject_job).outcome == "rejected"  # type: ignore[union-attr]
    assert not fake.pulls


def test_approval_from_reader_is_ignored() -> None:
    """Only users with write access may approve; the run stays paused."""
    fake, store, worker, _ = setup(APPROVAL)
    enqueue(store, TriggerKind.RUN, delivery="d-1")
    worker.run_pending()

    job = enqueue(store, TriggerKind.APPROVE, actor="visitor", delivery="d-2")
    worker.run_pending()

    assert store.get(job).outcome == UNAUTHORIZED  # type: ignore[union-attr]
    assert not fake.pulls


def test_relabel_while_paused_does_not_start_second_run() -> None:
    """Re-triggering a ticket whose run awaits approval is skipped with a pointer to approve/reject."""
    fake, store, worker, coder = setup(APPROVAL)
    enqueue(store, TriggerKind.RUN, delivery="d-1")
    worker.run_pending()

    again = enqueue(store, TriggerKind.RUN, delivery="d-2")
    worker.run_pending()

    assert store.get(again).outcome == SKIPPED  # type: ignore[union-attr]
    assert len(coder.calls) == 1
    assert "already has a change waiting for approval" in fake.comments[7][-1]


def test_approve_without_paused_run_is_skipped() -> None:
    """An approval comment when nothing is waiting gets a short explanation."""
    fake, store, worker, _ = setup()
    job = enqueue(store, TriggerKind.APPROVE)

    worker.run_pending()

    assert store.get(job).outcome == SKIPPED  # type: ignore[union-attr]
    assert "no AutoForge change waiting" in fake.comments[7][-1]


def test_trusted_cli_job_skips_authorization() -> None:
    """A job without an actor (the local CLI) is processed without a permission lookup."""
    fake, store, _, _ = setup()
    fake.permissions.clear()
    job = store.add(Trigger(TriggerKind.RUN, "7", "cli-1"), claim=True)
    assert job is not None

    result = process_job(job, worker_deps(fake), WorkflowConfig(), store)

    assert result.outcome == "pr_opened"
    assert not any("/collaborators/" in path for _, path in fake.calls)


def test_unexpected_error_marks_job_failed() -> None:
    """An exception outside the workflow's own error handling still finishes the job as failed."""
    fake, store, worker, _ = setup()
    fake.fail[("GET", r"/collaborators/.+/permission")] = 500
    job = enqueue(store, TriggerKind.RUN)

    worker.run_pending()

    stored = store.get(job)
    assert stored is not None and stored.status is JobStatus.FAILED and "GitHubError" in (stored.error or "")


def worker_deps(fake: FakeGitHub) -> Dependencies:
    """Fresh dependencies on ``fake`` (for calling process_job directly)."""
    gh = fake.client()
    return Dependencies(tickets=GitHubIssuesSource(gh), repo=GitHubRepoHost(gh, branch_prefix="autoforge/"),
                        coder=FakeCoder(), reviewer=FakeReviewer())
