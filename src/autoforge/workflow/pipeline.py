"""One ticket run: ticket -> context -> coder -> policy -> publish -> PR -> ticket feedback.

Step by step (see ``_run``):

1. **Idempotency check** - if an open AutoForge PR already exists for this
   ticket, stop (re-triggering must never create duplicate PRs).
2. **Load ticket** - if that fails there is nothing to report back on, so return.
3. Mark the ticket **in progress**.
4. **Build context** - snapshot of the repository for the agent.
5. **Generate** - the coder agent proposes a ``CodeChange``.
6. **Validate** - ``domain.policy`` rejects unsafe or inconsistent changes.
7. **Publish** - commit to ``autoforge/issue-N`` and open a pull request.
8. **Feedback** - comment the PR link (or the error) on the ticket and set its status.

Phase 1, increment 2 replaces this linear flow with a LangGraph graph (coder
<-> reviewer loop, checkpoints, human-in-the-loop interrupt) behind the same
``run_ticket`` entry point.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from enum import Enum

from autoforge.agents.context import build_snapshot
from autoforge.domain.models import CoderResult, Ticket, TicketStatus
from autoforge.domain.policy import validate_change
from autoforge.observability import run_context
from autoforge.ports import Coder, RepoHost, TicketSource

log = logging.getLogger(__name__)


class RunOutcome(str, Enum):
    """How a ticket run ended.

    - ``PR_OPENED``: a new pull request was opened.
    - ``ALREADY_OPEN``: an AutoForge PR for this ticket was already open; nothing done.
    - ``FAILED``: the run failed; the reason is in ``RunResult.error`` (and on the ticket, when possible).
    """

    PR_OPENED = "pr_opened"
    ALREADY_OPEN = "already_open"
    FAILED = "failed"


@dataclass(frozen=True)
class RunResult:
    """Result of one ``run_ticket`` call (printed as JSON by the CLI).

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
        max_context_bytes: Total size of file contents sent to the LLM.
        max_file_bytes: Largest single file that may be sent to the LLM.
    """

    branch_prefix: str = "autoforge/"
    max_context_bytes: int = 60_000
    max_file_bytes: int = 20_000


@dataclass(frozen=True)
class Dependencies:
    """The collaborators a run needs, passed in from ``app.wiring`` (or from tests).

    Attributes:
        tickets: Where the ticket is read from and status is reported to.
        repo: The repository code is read from and PRs are opened on.
        coder: The agent that writes the code change.
    """

    tickets: TicketSource
    repo: RepoHost
    coder: Coder


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
        deps: Ticket source, repository host and coder to use.
        config: Run settings.

    Returns:
        The run result (outcome, PR URL or error, run ID).
    """
    run_id = uuid.uuid4().hex[:12]
    with run_context(run_id):
        return _run(key, run_id, deps, config)


def _run(key: str, run_id: str, deps: Dependencies, config: WorkflowConfig) -> RunResult:
    """Execute the run steps described in the module docstring.

    Separated from ``run_ticket`` so that every log line here carries the run ID.
    """
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
    try:
        snapshot = build_snapshot(deps.repo, max_context_bytes=config.max_context_bytes,
                                  max_file_bytes=config.max_file_bytes)
        result = deps.coder.generate(ticket, snapshot)
        validate_change(result.change, snapshot)
        commit_sha = deps.repo.publish(snapshot, branch, result.change)
        pr = deps.repo.open_pull(
            title=result.change.pr_title,
            body=_pr_body(ticket, result, run_id),
            head=branch,
            base=snapshot.default_branch,
        )
    except Exception as exc:  # top-level boundary: report every failure back on the ticket
        log.exception("run failed", extra={"ticket": key})
        message = f"{type(exc).__name__}: {exc}"
        _safe_feedback(deps.tickets, key, TicketStatus.FAILED,
                       f"AutoForge could not complete this ticket.\n\n```\n{message[:1000]}\n```\n\nRun ID: `{run_id}`")
        return RunResult(run_id, RunOutcome.FAILED, error=message)

    log.info("pull request opened", extra={"pr": pr.url, "commit": commit_sha, "branch": branch,
                                           "files": len(result.change.files)})
    _safe_feedback(deps.tickets, key, TicketStatus.PR_OPENED,
                   f"AutoForge opened a pull request for review: {pr.url}\n\n"
                   f"{result.change.summary}\n\nRun ID: `{run_id}`")
    return RunResult(run_id, RunOutcome.PR_OPENED, pr_url=pr.url)


def _pr_body(ticket: Ticket, result: CoderResult, run_id: str) -> str:
    """Build the pull request description.

    It is the agent's own ``pr_body`` followed by a fixed footer: ``Closes #N``
    (links the PR to the ticket), the list of changed files, a reminder that a
    human must review, and run metadata (run ID, model, token usage).
    """
    files = "\n".join(f"- `{f.action}` {f.path}" for f in result.change.files)
    return f"""{result.change.pr_body}

---
Closes #{ticket.key}

**Files**
{files}

> [!IMPORTANT]
> AutoForge generated this pull request. A human must review it before it is merged.

<sub>AutoForge run `{run_id}` · model `{result.model}` · tokens in/out {result.usage.input_tokens}/{result.usage.output_tokens}</sub>
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
