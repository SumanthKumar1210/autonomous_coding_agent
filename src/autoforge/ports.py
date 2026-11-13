"""Ports: the interfaces the workflow depends on. Integrations implement them.

"Ports and adapters" in one sentence: the workflow says *what* it needs
(e.g. "get me the ticket", "open a PR") through these interfaces, and adapters
in ``integrations/`` and ``agents/`` say *how* (GitHub REST calls, OpenAI calls).

Benefits:

- the workflow never imports GitHub or OpenAI code, so it is testable with fakes;
- adding Jira means writing one new ``TicketSource`` - nothing else changes;
- the wiring of concrete classes happens in exactly one place: ``app/wiring.py``.

These are ``typing.Protocol`` classes: any object with matching methods
qualifies, no inheritance needed.
"""

from __future__ import annotations

from typing import Protocol

from autoforge.domain.models import (
    BaseRef,
    CodeChange,
    CoderResult,
    Job,
    PullRequest,
    RepoFile,
    RepoSnapshot,
    ReviewerResult,
    Revision,
    Ticket,
    TicketStatus,
    Trigger,
)


class TicketSource(Protocol):
    """Where work comes from and where progress is reported.

    Implemented by ``GitHubIssuesSource`` today; a Jira implementation can be
    added later.
    """

    def get_ticket(self, key: str) -> Ticket:
        """Load a ticket by its key.

        Raises:
            TicketError: If the ticket cannot be worked on (missing, closed, ...).
        """
        ...

    def comment(self, key: str, body: str) -> None:
        """Post a comment (Markdown) on the ticket."""
        ...

    def set_status(self, key: str, status: TicketStatus) -> None:
        """Mark the ticket with a progress status (replacing any previous one)."""
        ...

    def is_authorized(self, actor: str) -> bool:
        """Return True if ``actor`` may trigger AutoForge (has write access to the project).

        Hard constraint: only act on tickets labelled (and runs approved) by
        users with write access.
        """
        ...


class RepoHost(Protocol):
    """The target code repository (e.g. a GitHub repo).

    Deliberately has **no merge or approve operation**: AutoForge can open pull
    requests, but only a human can merge them.
    """

    @property
    def name(self) -> str:
        """Human-readable repository name, e.g. ``"owner/repo"`` (for logs)."""
        ...

    def get_base(self) -> BaseRef:
        """Return the current head commit of the default branch."""
        ...

    def list_files(self, base: BaseRef) -> list[RepoFile]:
        """List every file in the repository at ``base`` (metadata only)."""
        ...

    def read_file(self, file: RepoFile) -> bytes:
        """Download one file's raw content."""
        ...

    def find_open_pull(self, branch: str) -> PullRequest | None:
        """Return the open PR whose head is ``branch``, or ``None``."""
        ...

    def publish(self, snapshot: RepoSnapshot, branch: str, change: CodeChange) -> str:
        """Commit ``change`` on top of the snapshot's base and point ``branch`` at it.

        Returns:
            The new commit SHA.

        Raises:
            SafetyError: If ``branch`` is not an agent-owned branch.
        """
        ...

    def open_pull(self, *, title: str, body: str, head: str, base: str) -> PullRequest:
        """Open a pull request from ``head`` into ``base``."""
        ...


class Coder(Protocol):
    """An agent that turns a ticket + repository snapshot into a code change.

    Implemented by ``OpenAICoder``; tests use a ``FakeCoder``.
    """

    def generate(self, ticket: Ticket, snapshot: RepoSnapshot, revision: Revision | None = None) -> CoderResult:
        """Produce a code change for ``ticket``.

        Args:
            ticket: The ticket to implement.
            snapshot: Repository context.
            revision: ``None`` on the first attempt. On later attempts, the
                previous change and the feedback (reviewer findings or a policy
                error) the new change must address.

        Raises:
            AgentError: If the model refuses, is truncated, or returns nothing usable.
        """
        ...


class Reviewer(Protocol):
    """An agent that checks a proposed change against the ticket and the repository.

    Implemented by ``OpenAIReviewer``; tests use a ``FakeReviewer``. The
    reviewer only judges; it never edits the change itself.
    """

    def review(self, ticket: Ticket, snapshot: RepoSnapshot, change: CodeChange) -> ReviewerResult:
        """Return an approve / reject verdict with findings for ``change``.

        Raises:
            AgentError: If the model refuses, is truncated, or returns nothing usable.
        """
        ...


class JobStore(Protocol):
    """Durable queue of triggers, and the record of which run each one produced.

    Implemented by ``SqliteJobStore``. The webhook adds jobs; the worker
    claims and completes them. It is also how AutoForge finds the run waiting
    for approval on a ticket.
    """

    def add(self, trigger: Trigger, *, claim: bool = False) -> Job | None:
        """Store a new job.

        Args:
            trigger: The request.
            claim: ``True`` stores it already ``running`` (the caller processes
                it now, e.g. the CLI) so the worker never picks it up.

        Returns:
            The job, or ``None`` if a job with the same ``delivery_id`` exists
            (a redelivered webhook).
        """
        ...

    def has_pending(self, ticket_key: str, kind: str) -> bool:
        """Return True if a queued or running job of ``kind`` exists for the ticket."""
        ...

    def claim_next(self) -> Job | None:
        """Atomically take the oldest queued job and mark it running; ``None`` if the queue is empty."""
        ...

    def finish(self, job_id: int, *, run_id: str | None, outcome: str, error: str | None = None) -> None:
        """Record a job's result: ``done``, or ``failed`` when ``error`` is set."""
        ...

    def last_run(self, ticket_key: str) -> tuple[str, str] | None:
        """Return ``(run_id, outcome)`` of the most recent job that ran the ticket's workflow, if any."""
        ...

    def requeue_running(self) -> int:
        """Return jobs left ``running`` by a crash to ``queued`` (called at startup); return how many."""
        ...
