"""Tests for integrations.sqlite.job_store: the durable job queue and its idempotency rules."""

from __future__ import annotations

from pathlib import Path

from autoforge.domain.models import JobStatus, Trigger, TriggerKind
from autoforge.integrations.sqlite.job_store import SqliteJobStore


def run(key: str = "7", delivery: str = "d-1") -> Trigger:
    """A RUN trigger for ``key``."""
    return Trigger(TriggerKind.RUN, key, delivery, "maintainer")


def test_redelivered_webhook_is_stored_once() -> None:
    """A second job with the same delivery ID is refused."""
    store = SqliteJobStore(":memory:")

    assert store.add(run()) is not None
    assert store.add(run()) is None


def test_jobs_are_claimed_in_order_once() -> None:
    """claim_next returns the oldest queued job and never the same job twice."""
    store = SqliteJobStore(":memory:")
    first = store.add(run("1", "d-1"))
    store.add(run("2", "d-2"))

    claimed = store.claim_next()

    assert claimed is not None and first is not None and claimed.id == first.id
    assert claimed.status is JobStatus.RUNNING
    second = store.claim_next()
    assert second is not None and second.trigger.ticket_key == "2"
    assert store.claim_next() is None


def test_claimed_add_is_never_picked_by_worker() -> None:
    """A job added with claim=True (CLI) starts running and is not in the queue."""
    store = SqliteJobStore(":memory:")
    store.add(run(), claim=True)

    assert store.claim_next() is None
    assert store.has_pending("7", "run")


def test_finish_records_outcome_and_last_run() -> None:
    """finish stores the run ID and outcome; last_run returns the most recent run of the ticket."""
    store = SqliteJobStore(":memory:")
    a = store.add(run("7", "d-1"), claim=True)
    b = store.add(Trigger(TriggerKind.APPROVE, "7", "d-2", "maintainer"), claim=True)
    assert a is not None and b is not None

    store.finish(a.id, run_id="r1", outcome="awaiting_approval")
    assert store.last_run("7") == ("r1", "awaiting_approval")
    store.finish(b.id, run_id="r1", outcome="pr_opened")

    assert store.last_run("7") == ("r1", "pr_opened")
    assert not store.has_pending("7", "run")
    assert store.last_run("8") is None


def test_failed_jobs_are_marked_failed() -> None:
    """A job finished with an error is ``failed`` and keeps the error text."""
    store = SqliteJobStore(":memory:")
    job = store.add(run(), claim=True)
    assert job is not None

    store.finish(job.id, run_id=None, outcome="failed", error="boom")

    stored = store.get(job.id)
    assert stored is not None and stored.status is JobStatus.FAILED and stored.error == "boom"


def test_jobs_survive_restart_and_interrupted_jobs_are_requeued(tmp_path: Path) -> None:
    """Jobs persist in the file; a job left running by a crash is queued again on startup."""
    path = tmp_path / "jobs.sqlite"
    store = SqliteJobStore(path)
    store.add(run())
    store.claim_next()  # the process "crashes" while running it
    store.close()

    reopened = SqliteJobStore(path)

    assert reopened.requeue_running() == 1
    job = reopened.claim_next()
    assert job is not None and job.trigger == run()
