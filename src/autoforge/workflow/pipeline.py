"""One ticket run: start or resume the agent graph, then report back on the ticket.

``run_ticket`` (new run):

1. **Idempotency check** - if an open AutoForge PR already exists for this
   ticket, stop (re-triggering must never create duplicate PRs).
2. **Load ticket** - if that fails there is nothing to report back on, so return.
3. Mark the ticket **in progress**.
4. Run the **agent graph** (``workflow.graph``): context -> coder <-> reviewer
   loop -> optional human approval -> publish branch + PR.
5. **Feedback** - comment the outcome on the ticket and set its status label.

``resume_run`` continues a run that paused at the human approval gate
(``REQUIRE_APPROVAL=true``), with the human's approve / reject decision.

This module owns all ticket feedback; the graph owns the work. Any failure in
the graph (agent error, policy violation, review rejection, GitHub error) is
caught here, reported on the ticket, and returned as ``RunOutcome.FAILED``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from autoforge.domain.errors import ReviewRejectedError
from autoforge.domain.models import TicketStatus
from autoforge.observability import run_context
from autoforge.ports import Coder, RepoHost, Reviewer, TicketSource
from autoforge.workflow.graph import (
    AgentGraph,
    Checkpointer,
    GraphSettings,
    RunState,
    build_graph,
    checkpoint_serializer,
)

log = logging.getLogger(__name__)


class RunOutcome(str, Enum):
    """How a ticket run ended.

    - ``PR_OPENED``: a new pull request was opened.
    - ``ALREADY_OPEN``: an AutoForge PR for this ticket was already open; nothing done.
    - ``AWAITING_APPROVAL``: the change passed review and waits for a human (``resume_run``).
    - ``REJECTED``: a human rejected the change at the approval gate; no PR.
    - ``FAILED``: the run failed; the reason is in ``RunResult.error`` (and on the ticket, when possible).
    """

    PR_OPENED = "pr_opened"
    ALREADY_OPEN = "already_open"
    AWAITING_APPROVAL = "awaiting_approval"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True)
class RunResult:
    """Result of one ``run_ticket`` / ``resume_run`` call (printed as JSON by the CLI).

    Attributes:
        run_id: Unique ID of this run; also appears in logs, the PR and the ticket comment.
        outcome: How the run ended.
        pr_url: Link to the new or existing PR, if any.
        error: ``"ErrorType: message"`` when the run failed.
    """

    run_id: str
    outcome: RunOutcome
    pr_url: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class WorkflowConfig:
    """Tunable settings for a run (filled from ``app.config.Settings``).

    Attributes:
        branch_prefix: Prefix of agent branches, e.g. ``"autoforge/"``.
        max_context_bytes: Total size of file contents sent to the LLMs.
        max_file_bytes: Largest single file that may be sent to the LLMs.
        max_attempts: Maximum coder attempts per run (first try + revisions).
        require_approval: Pause for a human before publishing the PR.
    """

    branch_prefix: str = "autoforge/"
    max_context_bytes: int = 60_000
    max_file_bytes: int = 20_000
    max_attempts: int = 3
    require_approval: bool = False


@dataclass(frozen=True)
class Dependencies:
    """The collaborators a run needs, passed in from ``app.wiring`` (or from tests).

    Attributes:
        tickets: Where the ticket is read from and status is reported to.
        repo: The repository code is read from and PRs are opened on.
        coder: The agent that writes the code change.
        reviewer: The agent that reviews the change.
        checkpointer: Where run state is saved. Production uses SQLite so a
            paused run survives a restart; the in-memory default suits tests.
    """

    tickets: TicketSource
    repo: RepoHost
    coder: Coder
    reviewer: Reviewer
    checkpointer: Checkpointer = field(default_factory=lambda: InMemorySaver(serde=checkpoint_serializer()))


def branch_for(ticket_key: str, prefix: str) -> str:
    """Return the agent branch name for a ticket.

    The name is deterministic (always the same for the same ticket), which is
    what makes the "PR already open?" idempotency check possible.

    Example:
        ``branch_for("#7", "autoforge/")`` -> ``"autoforge/issue-7"``
    """
    return f"{prefix}issue-{ticket_key.lstrip('#')}"


def run_ticket(key: str, deps: Dependencies, config: WorkflowConfig) -> RunResult:
    """Run AutoForge on one ticket, end to end. This is the main entry point.

    Never raises for expected failures: every problem is turned into a
    ``RunResult`` with ``outcome=FAILED`` and, where possible, a comment on the
    ticket explaining what went wrong.

    Args:
        key: Ticket key, e.g. ``"12"`` for GitHub issue #12.
        deps: Ticket source, repository host, agents and checkpointer to use.
        config: Run settings.

    Returns:
        The run result (outcome, PR URL or error, run ID).
    """
    run_id = uuid.uuid4().hex[:12]
    with run_context(run_id):
        log.info("run started", extra={"ticket": key, "repo": deps.repo.name})
        branch = branch_for(key, config.branch_prefix)

        # Idempotency: one open agent PR per ticket. Re-triggers must not open duplicates.
        if existing := deps.repo.find_open_pull(branch):
            log.info("open PR already exists; skipping", extra={"pr": existing.url})
            return RunResult(run_id, RunOutcome.ALREADY_OPEN, pr_url=existing.url)

        try:
            ticket = deps.tickets.get_ticket(key)
        except Exception as exc:  # ticket missing/closed/unreadable: nothing to report back on
            log.exception("could not load ticket", extra={"ticket": key})
            return RunResult(run_id, RunOutcome.FAILED, error=f"{type(exc).__name__}: {exc}")

        deps.tickets.set_status(key, TicketStatus.IN_PROGRESS)
        initial: RunState = {"run_id": run_id, "ticket_key": key, "branch": branch, "ticket": ticket}
        return _drive(key, run_id, deps, config, lambda graph, cfg: graph.invoke(initial, cfg))


def resume_run(run_id: str, approved: bool, deps: Dependencies, config: WorkflowConfig) -> RunResult:
    """Continue a run paused at the human approval gate.

    Args:
        run_id: The run ID printed when the run paused (also on the ticket).
        approved: ``True`` publishes the PR; ``False`` discards the change.
        deps: Must use the same checkpointer (database) as the paused run.
        config: Run settings.

    Returns:
        ``PR_OPENED`` / ``REJECTED`` on success, or ``FAILED`` if the run is
        unknown, not paused, or publishing failed.
    """
    with run_context(run_id):
        graph = _graph(deps, config)
        snapshot = graph.get_state(_thread(run_id))
        if not snapshot.values:
            return RunResult(run_id, RunOutcome.FAILED, error=f"Unknown run id: {run_id}")
        if "approve" not in snapshot.next:
            return RunResult(run_id, RunOutcome.FAILED, error=f"Run {run_id} is not awaiting approval")

        key = snapshot.values["ticket_key"]
        log.info("resuming run", extra={"ticket": key, "approved": approved})
        if approved and (existing := deps.repo.find_open_pull(snapshot.values["branch"])):
            log.info("open PR already exists; skipping", extra={"pr": existing.url})
            return RunResult(run_id, RunOutcome.ALREADY_OPEN, pr_url=existing.url)
        return _drive(key, run_id, deps, config,
                      lambda g, cfg: g.invoke(Command(resume={"approved": approved}), cfg))


def is_awaiting_approval(run_id: str, deps: Dependencies, config: WorkflowConfig) -> bool:
    """Return True if run ``run_id`` is currently paused at the human approval gate.

    The checkpoint is the source of truth: a run already resumed (e.g. from
    the CLI) is no longer paused, whatever the job queue last recorded.
    """
    return "approve" in _graph(deps, config).get_state(_thread(run_id)).next


# ----------------------------------------------------------------- internals

Invoker = Callable[[AgentGraph, RunnableConfig], Any]
"""Starts or resumes the graph: ``(graph, config) -> final state``."""


def _drive(key: str, run_id: str, deps: Dependencies, config: WorkflowConfig, invoke: Invoker) -> RunResult:
    """Run the graph via ``invoke``, then report the outcome on the ticket.

    This is the top-level error boundary: any exception from the graph is
    logged, posted on the ticket, and returned as ``FAILED``.
    """
    graph = _graph(deps, config)
    cfg = _thread(run_id)
    try:
        invoke(graph, cfg)
    except Exception as exc:  # top-level boundary: report every failure back on the ticket
        log.exception("run failed", extra={"ticket": key})
        message = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, ReviewRejectedError):
            comment = f"AutoForge could not complete this ticket.\n\n{str(exc)[:3000]}\n\nRun ID: `{run_id}`"
        else:
            comment = f"AutoForge could not complete this ticket.\n\n```\n{message[:1000]}\n```\n\nRun ID: `{run_id}`"
        _safe_feedback(deps.tickets, key, TicketStatus.FAILED, comment)
        return RunResult(run_id, RunOutcome.FAILED, error=message)

    final = graph.get_state(cfg)
    state: RunState = final.values  # type: ignore[assignment]

    if "approve" in final.next:  # paused at the human-in-the-loop interrupt
        log.info("awaiting human approval", extra={"ticket": key})
        _safe_feedback(deps.tickets, key, TicketStatus.AWAITING_APPROVAL,
                       f"AutoForge's change passed automated review and is waiting for approval.\n\n"
                       f"{state['change'].summary}\n\n{_file_list(state)}\n\n"
                       f"A maintainer can comment `/autoforge approve` to open the pull request or "
                       f"`/autoforge reject` to discard the change (CLI: `autoforge resume {run_id}`).\n\n"
                       f"Run ID: `{run_id}`")
        return RunResult(run_id, RunOutcome.AWAITING_APPROVAL)

    if state.get("human_rejected"):
        _safe_feedback(deps.tickets, key, TicketStatus.FAILED,
                       f"The proposed change was rejected at the approval step; no pull request was opened.\n\n"
                       f"Run ID: `{run_id}`")
        return RunResult(run_id, RunOutcome.REJECTED)

    pr = state["pr"]
    log.info("pull request opened", extra={"pr": pr.url, "commit": state["commit_sha"], "branch": state["branch"],
                                           "files": len(state["change"].files), "attempts": state["attempts"],
                                           "input_tokens": state["usage"].input_tokens,
                                           "output_tokens": state["usage"].output_tokens})
    _safe_feedback(deps.tickets, key, TicketStatus.PR_OPENED,
                   f"AutoForge opened a pull request for review: {pr.url}\n\n"
                   f"{state['change'].summary}\n\nRun ID: `{run_id}`")
    return RunResult(run_id, RunOutcome.PR_OPENED, pr_url=pr.url)


def _graph(deps: Dependencies, config: WorkflowConfig) -> AgentGraph:
    """Build the agent graph from the dependencies and config."""
    return build_graph(
        repo=deps.repo, coder=deps.coder, reviewer=deps.reviewer, checkpointer=deps.checkpointer,
        settings=GraphSettings(max_context_bytes=config.max_context_bytes, max_file_bytes=config.max_file_bytes,
                               max_attempts=config.max_attempts, require_approval=config.require_approval),
        pr_body=_pr_body,
    )


def _thread(run_id: str) -> RunnableConfig:
    """LangGraph run config: the run ID is the checkpoint thread ID."""
    return {"configurable": {"thread_id": run_id}}


def _file_list(state: RunState) -> str:
    """Markdown bullet list of the change's file operations."""
    return "\n".join(f"- `{f.action}` {f.path}" for f in state["change"].files)


def _pr_body(state: RunState) -> str:
    """Build the pull request description.

    It is the agent's own ``pr_body`` followed by a fixed footer: ``Closes #N``
    (links the PR to the ticket), the list of changed files, the automated
    review outcome (with any remaining minor findings), a reminder that a human
    must review, and run metadata (run ID, models, attempts, token usage).
    """
    change, verdict, usage = state["change"], state["verdict"], state["usage"]
    notes = "\n".join(f"- `{f.path or 'whole change'}`: {f.message}" for f in verdict.findings)
    review = f"Approved by the AutoForge reviewer after {state['attempts']} coder attempt(s).\n\n{verdict.summary}"
    if notes:
        review += f"\n\nReviewer notes (non-blocking):\n{notes}"
    meta = (f"AutoForge run `{state['run_id']}` · coder `{state['coder_model']}` · "
            f"reviewer `{state['reviewer_model']}` · tokens in/out {usage.input_tokens}/{usage.output_tokens}")
    return f"""{change.pr_body}

---
Closes #{state['ticket_key'].lstrip('#')}

**Files**
{_file_list(state)}

**Automated review**
{review}

> [!IMPORTANT]
> AutoForge generated this pull request. A human must review it before it is merged.

<sub>{meta}</sub>
"""


def _safe_feedback(tickets: TicketSource, key: str, status: TicketStatus, comment: str) -> None:
    """Post a comment and status on the ticket without ever raising.

    If reporting itself fails (e.g. the token lacks the Issues permission), the
    error is logged and the run result is still returned to the caller.
    """
    try:
        tickets.comment(key, comment)
        tickets.set_status(key, status)
    except Exception:
        log.exception("failed to post feedback to ticket", extra={"ticket": key, "status": status.value})
