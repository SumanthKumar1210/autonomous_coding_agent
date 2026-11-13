"""Durable job queue in SQLite: a ``ports.JobStore``.

One table, ``jobs``. Each row is a trigger (from the webhook or the CLI) and
what became of it. Why SQLite: the service runs as one container, jobs must
survive restarts, and no extra infrastructure is needed. The file lives on the
same volume as the LangGraph checkpoints.

Idempotency lives here too:

- ``delivery_id`` is ``UNIQUE``, so a webhook GitHub redelivers is stored once;
- ``has_pending`` lets the webhook skip a second RUN for a ticket already queued;
- ``last_run`` tells the worker whether a ticket's latest run is paused for approval.

The store is shared by the web server and the worker thread, so every
operation holds a lock and runs in its own transaction.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from autoforge.domain.models import Job, JobStatus, Trigger, TriggerKind

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,
    ticket_key  TEXT NOT NULL,
    actor       TEXT,
    status      TEXT NOT NULL,
    run_id      TEXT,
    outcome     TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status, id);
CREATE INDEX IF NOT EXISTS jobs_ticket ON jobs (ticket_key, id);
"""

_COLUMNS = "id, delivery_id, kind, ticket_key, actor, status, run_id, outcome, error"


class SqliteJobStore:
    """``ports.JobStore`` backed by a SQLite file.

    Example::

        store = SqliteJobStore(".autoforge/jobs.sqlite")
        store.add(Trigger(TriggerKind.RUN, "12", "delivery-1", "alice"))
        job = store.claim_next()
    """

    def __init__(self, path: str | Path) -> None:
        """Open (creating if needed) the database at ``path``; ``":memory:"`` works for tests."""
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()

    def add(self, trigger: Trigger, *, claim: bool = False) -> Job | None:
        """Store a job; ``None`` if its ``delivery_id`` was already stored (redelivery). See ``ports.JobStore``."""
        status = JobStatus.RUNNING if claim else JobStatus.QUEUED
        now = _now()
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT INTO jobs (delivery_id, kind, ticket_key, actor, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (trigger.delivery_id, trigger.kind.value, trigger.ticket_key, trigger.actor, status.value,
                     now, now),
                )
            except sqlite3.IntegrityError:
                return None
        return Job(id=int(cur.lastrowid or 0), trigger=trigger, status=status)

    def has_pending(self, ticket_key: str, kind: str) -> bool:
        """Return True if a queued or running job of ``kind`` exists for the ticket."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM jobs WHERE ticket_key = ? AND kind = ? AND status IN (?, ?) LIMIT 1",
                (ticket_key, kind, JobStatus.QUEUED.value, JobStatus.RUNNING.value),
            ).fetchone()
        return row is not None

    def claim_next(self) -> Job | None:
        """Atomically take the oldest queued job and mark it running; ``None`` if the queue is empty."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    f"SELECT {_COLUMNS} FROM jobs WHERE status = ? ORDER BY id LIMIT 1",  # noqa: S608 (constant)
                    (JobStatus.QUEUED.value,),
                ).fetchone()
                if row is None:
                    self._conn.execute("COMMIT")
                    return None
                self._conn.execute("UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                                   (JobStatus.RUNNING.value, _now(), row[0]))
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        job = _job(row)
        return Job(id=job.id, trigger=job.trigger, status=JobStatus.RUNNING)

    def finish(self, job_id: int, *, run_id: str | None, outcome: str, error: str | None = None) -> None:
        """Record a job's result: ``done``, or ``failed`` when ``error`` is set."""
        status = JobStatus.FAILED if error else JobStatus.DONE
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status = ?, run_id = ?, outcome = ?, error = ?, updated_at = ? WHERE id = ?",
                (status.value, run_id, outcome, error, _now(), job_id),
            )

    def last_run(self, ticket_key: str) -> tuple[str, str] | None:
        """Return ``(run_id, outcome)`` of the most recent job that ran the ticket's workflow, if any."""
        with self._lock:
            row = self._conn.execute(
                "SELECT run_id, outcome FROM jobs WHERE ticket_key = ? AND run_id IS NOT NULL "
                "AND outcome IS NOT NULL ORDER BY id DESC LIMIT 1",
                (ticket_key,),
            ).fetchone()
        return (str(row[0]), str(row[1])) if row else None

    def requeue_running(self) -> int:
        """Return jobs left ``running`` by a crash to ``queued``; return how many."""
        with self._lock:
            cur = self._conn.execute("UPDATE jobs SET status = ?, updated_at = ? WHERE status = ?",
                                     (JobStatus.QUEUED.value, _now(), JobStatus.RUNNING.value))
        return cur.rowcount

    def get(self, job_id: int) -> Job | None:
        """Return one job by ID (for tests and diagnostics)."""
        with self._lock:
            row = self._conn.execute(f"SELECT {_COLUMNS} FROM jobs WHERE id = ?",  # noqa: S608 (constant)
                                     (job_id,)).fetchone()
        return _job(row) if row else None


def _job(row: tuple[object, ...]) -> Job:
    """Build a ``Job`` from a ``_COLUMNS`` row."""
    job_id, delivery_id, kind, ticket_key, actor, status, run_id, outcome, error = row
    trigger = Trigger(TriggerKind(str(kind)), str(ticket_key), str(delivery_id), None if actor is None else str(actor))
    return Job(id=int(str(job_id)), trigger=trigger, status=JobStatus(str(status)),
               run_id=None if run_id is None else str(run_id),
               outcome=None if outcome is None else str(outcome),
               error=None if error is None else str(error))


def _now() -> str:
    """Current UTC time as ISO-8601 text."""
    return datetime.now(UTC).isoformat()
