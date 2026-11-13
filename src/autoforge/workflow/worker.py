"""Job processing: turn a queued ``Trigger`` into a workflow run, and the background worker.

The webhook only verifies, parses and enqueues (GitHub wants a reply within
10 seconds; a run takes much longer). This worker does the slow part, one job
at a time, in a background thread of the service:

1. **Authorize** - the actor who added the label or wrote the comment must have
   write access (``TicketSource.is_authorized``). Otherwise the job is dropped
   silently (outcome ``unauthorized``); commenting would let anyone spam issues.
2. **RUN** - start ``run_ticket``, unless the ticket's latest run is still
   paused for approval (that would create a second, competing change).
3. **APPROVE / REJECT** - find the ticket's paused run and ``resume_run`` it.

Every job ends ``done`` or ``failed`` in the job store with its run ID and
outcome, which is also how a later approval finds the paused run.

The CLI uses ``process_job`` too, so both entry points behave the same.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from autoforge.domain.models import Job, TriggerKind
from autoforge.ports import JobStore
from autoforge.workflow.pipeline import (
    Dependencies,
    RunOutcome,
    WorkflowConfig,
    is_awaiting_approval,
    resume_run,
    run_ticket,
)

log = logging.getLogger(__name__)

UNAUTHORIZED = "unauthorized"
"""Job outcome: the actor lacks write access; nothing was done."""

SKIPPED = "skipped"
"""Job outcome: nothing to do (a run is already paused, or no run is waiting for approval)."""


@dataclass(frozen=True)
class JobResult:
    """What processing one job produced (printed as JSON by the CLI).

    Attributes:
        outcome: A ``RunOutcome`` value, ``"unauthorized"`` or ``"skipped"``.
        run_id: The run started or resumed, if any.
        pr_url: Link to the PR, if one was opened or already open.
        error: Failure reason, if the run failed.
    """

    outcome: str
    run_id: str | None = None
    pr_url: str | None = None
    error: str | None = None


def process_job(job: Job, deps: Dependencies, config: WorkflowConfig, store: JobStore) -> JobResult:
    """Process one claimed job and record its result in ``store``. Never raises.

    Args:
        job: A job already marked ``running`` (claimed by the worker or added
            with ``claim=True`` by the CLI).
        deps: Workflow collaborators.
        config: Run settings.
        store: Where the job's result is recorded.

    Returns:
        The job result.
    """
    trigger = job.trigger
    log.info("processing job", extra={"job": job.id, "kind": trigger.kind.value, "ticket": trigger.ticket_key,
                                      "actor": trigger.actor, "delivery": trigger.delivery_id})
    try:
        result = _dispatch(job, deps, config, store)
    except Exception as exc:  # boundary: a job must always be finished, never left "running"
        log.exception("job failed", extra={"job": job.id})
        result = JobResult(RunOutcome.FAILED.value, error=f"{type(exc).__name__}: {exc}")
    store.finish(job.id, run_id=result.run_id, outcome=result.outcome, error=result.error)
    log.info("job finished", extra={"job": job.id, "outcome": result.outcome, "run_id": result.run_id})
    return result


def _dispatch(job: Job, deps: Dependencies, config: WorkflowConfig, store: JobStore) -> JobResult:
    """Authorize the actor, then run or resume the ticket's workflow."""
    trigger = job.trigger
    key = trigger.ticket_key
    if trigger.actor is not None and not deps.tickets.is_authorized(trigger.actor):
        log.warning("actor lacks write access; ignoring", extra={"actor": trigger.actor, "ticket": key})
        return JobResult(UNAUTHORIZED)

    paused_run = _paused_run(key, deps, config, store)

    if trigger.kind is TriggerKind.RUN:
        if paused_run:
            deps.tickets.comment(key, "AutoForge already has a change waiting for approval on this issue. Comment "
                                      "`/autoforge approve` or `/autoforge reject` to decide it first.")
            return JobResult(SKIPPED, run_id=paused_run)
        result = run_ticket(key, deps, config)
    else:
        if not paused_run:
            deps.tickets.comment(key, "There is no AutoForge change waiting for approval on this issue.")
            return JobResult(SKIPPED)
        log.info("approval decision received", extra={"run": paused_run, "kind": trigger.kind.value})
        result = resume_run(paused_run, trigger.kind is TriggerKind.APPROVE, deps, config)
    return JobResult(result.outcome.value, result.run_id, result.pr_url, result.error)


def _paused_run(ticket_key: str, deps: Dependencies, config: WorkflowConfig, store: JobStore) -> str | None:
    """Return the ID of the ticket's run paused for approval, or ``None``.

    The job store says which run was last; the checkpoint confirms it is still
    paused (it may have been resumed from the CLI meanwhile).
    """
    last = store.last_run(ticket_key)
    if last and last[1] == RunOutcome.AWAITING_APPROVAL.value and is_awaiting_approval(last[0], deps, config):
        return last[0]
    return None


class Worker:
    """Background thread that processes queued jobs one at a time.

    Started and stopped by the webhook service's lifespan::

        worker = Worker(store, deps, config)
        worker.start()
        ...
        worker.stop()   # finishes the current job, then exits

    Jobs run sequentially, so two runs never write to the same repository at
    once, and the GitHub / OpenAI clients are only used from this thread.
    """

    def __init__(self, store: JobStore, deps: Dependencies, config: WorkflowConfig, *,
                 poll_seconds: float = 2.0) -> None:
        """Create the worker.

        Args:
            store: The job queue.
            deps: Workflow collaborators (shared across jobs).
            config: Run settings.
            poll_seconds: How long to wait when the queue is empty (``wake`` interrupts the wait).
        """
        self._store = store
        self._deps = deps
        self._config = config
        self._poll = poll_seconds
        self._stop = threading.Event()
        self._wakeup = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="autoforge-worker", daemon=True)

    def start(self) -> None:
        """Requeue jobs interrupted by a previous crash, then start the thread."""
        if requeued := self._store.requeue_running():
            log.warning("requeued interrupted jobs", extra={"count": requeued})
        self._thread.start()

    def is_alive(self) -> bool:
        """Return True while the worker thread is running (used by the health check)."""
        return self._thread.is_alive()

    def wake(self) -> None:
        """Signal that a job was enqueued, so it starts without waiting for the next poll."""
        self._wakeup.set()

    def stop(self, timeout: float | None = None) -> None:
        """Ask the thread to exit after the current job and wait up to ``timeout`` seconds."""
        self._stop.set()
        self._wakeup.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def run_pending(self) -> int:
        """Process queued jobs until the queue is empty; return how many ran (used by the loop and tests)."""
        count = 0
        while not self._stop.is_set() and (job := self._store.claim_next()):
            process_job(job, self._deps, self._config, self._store)
            count += 1
        return count

    def _loop(self) -> None:
        """Thread body: drain the queue, then sleep until woken or the poll interval passes."""
        while not self._stop.is_set():
            try:
                self.run_pending()
            except Exception:  # e.g. the database is locked; keep the worker alive
                log.exception("worker loop error")
            self._wakeup.wait(self._poll)
            self._wakeup.clear()
