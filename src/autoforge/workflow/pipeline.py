"""Ticket run: ticket -> context -> coder -> policy -> publish -> PR -> ticket feedback.

Increment 2 replaces this linear flow with a LangGraph graph (coder <-> reviewer
loop, checkpoints, HITL interrupt) behind the same `run_ticket` entry point.
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
    PR_OPENED = "pr_opened"
    ALREADY_OPEN = "already_open"
    FAILED = "failed"


@dataclass(frozen=True)
class RunResult:
    run_id: str
    outcome: RunOutcome
    pr_url: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class WorkflowConfig:
    branch_prefix: str = "autoforge/"
    max_context_bytes: int = 60_000
    max_file_bytes: int = 20_000


@dataclass(frozen=True)
class Dependencies:
    tickets: TicketSource
    repo: RepoHost
    coder: Coder


def branch_for(ticket_key: str, prefix: str) -> str:
    return f"{prefix}issue-{ticket_key.lstrip('#')}"


def run_ticket(key: str, deps: Dependencies, config: WorkflowConfig) -> RunResult:
    run_id = uuid.uuid4().hex[:12]
    with run_context(run_id):
        return _run(key, run_id, deps, config)


def _run(key: str, run_id: str, deps: Dependencies, config: WorkflowConfig) -> RunResult:
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
    try:
        tickets.comment(key, comment)
        tickets.set_status(key, status)
    except Exception:
        log.exception("failed to post feedback to ticket", extra={"ticket": key, "status": status.value})
