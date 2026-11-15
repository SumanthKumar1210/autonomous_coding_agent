"""The agent graph (LangGraph): coder -> checks -> reviewer loop, approval gate, publish.

::

    prepare ──► code ──► validate ──► verify ──► review ──► [approve] ──► publish ──► END
                 ▲           │           │           │          │
                 └─ policy ──┘           │           │          └─ human rejects ──► END
                 └──── failing checks ───┘           │
                 └───────────── review findings ─────┘

Nodes (each one step, each returns a ``Command`` naming the next node):

- **prepare** - build the repository snapshot for the agents and choose the
  checks (``domain.checks``, from the default branch).
- **code** - the coder proposes a change (on a retry: with ``Revision`` feedback).
- **validate** - ``domain.policy`` checks the change. A violation goes back to
  the coder as feedback (it is a correctable agent mistake), until attempts run out.
- **verify** - the ``Verifier`` (sandbox container) applies the change to the
  base commit and runs the checks. A check the change broke goes back to the
  coder with its output; failures that also exist on the base do not count.
  Skipped (and said so in the PR) when no sandbox or no checks are configured.
- **review** - the reviewer judges the change. Rejected -> back to the coder
  with the findings; still rejected after ``max_attempts`` -> ``ReviewRejectedError``.
- **approve** - only if ``require_approval``: the graph *interrupts* here and
  waits for a human (``autoforge resume <run_id>``). The run's state is
  checkpointed, so the process may exit and resume much later.
- **publish** - commit to the agent branch and open the pull request.

The graph only does the work. Reporting back on the ticket (comments, status
labels) stays in ``workflow.pipeline``, which runs and resumes this graph.

Every step is checkpointed under ``thread_id = run_id``. Checkpoints store
domain objects, so the serializer is given an explicit allow-list of the types
it may restore (``CHECKPOINT_TYPES``); anything else is refused.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from autoforge.agents.context import build_snapshot
from autoforge.domain.checks import CHECKS_CONFIG, checks_for
from autoforge.domain.errors import ChangeValidationError, ChecksFailedError, ReviewRejectedError
from autoforge.domain.models import (
    BaseRef,
    CheckResult,
    CheckSpec,
    CodeChange,
    ContextFile,
    FileChange,
    PullRequest,
    RepoSnapshot,
    ReviewFinding,
    ReviewVerdict,
    Revision,
    Ticket,
    TokenUsage,
    VerificationReport,
)
from autoforge.domain.policy import validate_change
from autoforge.ports import Coder, RepoHost, Reviewer, Verifier

log = logging.getLogger(__name__)

CHECKPOINT_TYPES: tuple[type, ...] = (
    Ticket, BaseRef, RepoSnapshot, ContextFile, CodeChange, FileChange, ReviewVerdict, ReviewFinding, Revision,
    TokenUsage,
    PullRequest, CheckSpec, CheckResult, VerificationReport,
)
"""Every non-builtin type stored in ``RunState``; the only types a checkpoint may restore."""


def checkpoint_serializer() -> JsonPlusSerializer:
    """Return the checkpoint serializer, restricted to ``CHECKPOINT_TYPES``.

    Use it for every checkpointer (SQLite in production, in-memory in tests)
    so a tampered or stale checkpoint cannot instantiate arbitrary classes.
    """
    return JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)


class RunState(TypedDict, total=False):
    """Everything a run knows, saved in a checkpoint after every node.

    Set by ``pipeline`` at the start: ``run_id``, ``ticket_key``, ``branch``,
    ``ticket``. Filled in by the nodes as the run proceeds.

    Attributes:
        run_id: The run ID (also the checkpoint thread ID).
        ticket_key: Ticket key, e.g. ``"12"``.
        branch: Agent branch the change will be published to.
        ticket: The loaded ticket.
        snapshot: Repository context shown to both agents.
        checks: Checks chosen from the default branch (empty: none, or no sandbox).
        change: The coder's latest proposed change.
        revision: Feedback for the coder's next attempt (``None`` once addressed).
        verification: Result of running the checks on the latest change.
        verdict: The reviewer's latest verdict.
        attempts: Number of coder attempts so far.
        usage: Total token usage of all LLM calls in the run.
        coder_model: Model ID of the coder.
        reviewer_model: Model ID of the reviewer.
        human_rejected: True if a human rejected the change at the approval gate.
        pr: The pull request, once opened.
        commit_sha: The published commit, once pushed.
    """

    run_id: str
    ticket_key: str
    branch: str
    ticket: Ticket
    snapshot: RepoSnapshot
    checks: list[CheckSpec]
    change: CodeChange
    revision: Revision | None
    verification: VerificationReport
    verdict: ReviewVerdict
    attempts: int
    usage: TokenUsage
    coder_model: str
    reviewer_model: str
    human_rejected: bool
    pr: PullRequest
    commit_sha: str


@dataclass(frozen=True)
class GraphSettings:
    """Run settings the graph needs.

    Attributes:
        max_context_bytes: Total size of file contents sent to the LLMs.
        max_file_bytes: Largest single file that may be sent to the LLMs.
        max_attempts: Maximum coder attempts (first try + revisions).
        require_approval: Pause for a human before publishing.
    """

    max_context_bytes: int
    max_file_bytes: int
    max_attempts: int
    require_approval: bool


AgentGraph = CompiledStateGraph[Any, Any, Any, Any]
"""The compiled agent graph returned by ``build_graph``."""

Checkpointer = BaseCheckpointSaver[Any]
"""Any LangGraph checkpoint saver (SQLite in production, in-memory in tests)."""

PrBodyBuilder = Callable[[RunState], str]
"""Function that builds the PR description (supplied by the pipeline)."""


def build_graph(
    *,
    repo: RepoHost,
    coder: Coder,
    reviewer: Reviewer,
    checkpointer: Checkpointer,
    settings: GraphSettings,
    pr_body: PrBodyBuilder,
    verifier: Verifier | None = None,
) -> AgentGraph:
    """Build and compile the agent graph.

    Args:
        repo: Repository host (read context, publish, open PR).
        coder: The coder agent.
        reviewer: The reviewer agent.
        checkpointer: Where run state is saved after every node.
        settings: Attempt limit, approval gate, context budget.
        pr_body: Builds the PR description from the final state.
        verifier: Runs the repository's checks in the sandbox; ``None`` skips verification.

    Returns:
        The compiled graph. Run it with
        ``graph.invoke(state, {"configurable": {"thread_id": run_id}})``.
    """

    archives: dict[str, bytes] = {}  # base commit -> archive, downloaded once per run for all attempts

    def prepare(state: RunState) -> Command[Literal["code"]]:
        """Build the snapshot both agents will see (context chosen for the ticket) and choose the checks."""
        snapshot = build_snapshot(repo, state["ticket"], max_context_bytes=settings.max_context_bytes,
                                  max_file_bytes=settings.max_file_bytes)
        checks = _choose_checks(repo, snapshot.base) if verifier is not None else []
        if verifier is not None:
            log.info("checks chosen", extra={"checks": [c.name for c in checks]})
        return Command(goto="code", update={"snapshot": snapshot, "checks": checks, "attempts": 0,
                                            "usage": TokenUsage(), "revision": None})

    def code(state: RunState) -> Command[Literal["validate"]]:
        """Ask the coder for a change (with feedback, if this is a revision)."""
        attempt = state["attempts"] + 1
        log.info("coder attempt", extra={"attempt": attempt, "revision": state.get("revision") is not None})
        result = coder.generate(state["ticket"], state["snapshot"], state.get("revision"))
        return Command(goto="validate", update={
            "change": result.change, "attempts": attempt, "usage": state["usage"] + result.usage,
            "coder_model": result.model, "revision": None,
        })

    def validate(state: RunState) -> Command[Literal["verify", "code"]]:
        """Check the change against the change policy; send violations back to the coder."""
        try:
            validate_change(state["change"], state["snapshot"])
        except ChangeValidationError as exc:
            log.warning("change rejected by policy", extra={"attempt": state["attempts"], "error": str(exc)})
            if state["attempts"] >= settings.max_attempts:
                raise
            feedback = f"Change policy violation (the change was not accepted): {exc}"
            return Command(goto="code", update={"revision": Revision(state["change"], feedback)})
        return Command(goto="verify")

    def verify(state: RunState) -> Command[Literal["review", "code"]]:
        """Run the checks on the change in the sandbox; send checks it broke back to the coder."""
        checks = state.get("checks", [])
        if verifier is None:
            report = VerificationReport(skipped="no verification sandbox configured (SANDBOX_URL)")
        elif not checks:
            report = VerificationReport(skipped="no checks configured or detected")
        else:
            base = state["snapshot"].base
            if base.commit_sha not in archives:
                archives.clear()
                archives[base.commit_sha] = repo.download_archive(base)
            report = verifier.verify(archives[base.commit_sha], state["change"], checks)
        log.info("verification", extra={"attempt": state["attempts"], "passed": report.passed,
                                        "skipped": report.skipped or None,
                                        "checks": {c.name: c.status + (" (preexisting)" if c.preexisting else "")
                                                   for c in report.checks}})
        if report.passed:
            return Command(goto="review", update={"verification": report})
        if state["attempts"] >= settings.max_attempts:
            raise ChecksFailedError(_checks_failed_message(report, state["attempts"]))
        return Command(goto="code", update={"verification": report,
                                            "revision": Revision(state["change"], format_check_failures(report))})

    def review(state: RunState) -> Command[Literal["approve", "publish", "code"]]:
        """Ask the reviewer for a verdict; loop back to the coder on rejection."""
        result = reviewer.review(state["ticket"], state["snapshot"], state["change"])
        verdict = result.verdict
        update: dict[str, Any] = {"verdict": verdict, "usage": state["usage"] + result.usage,
                                  "reviewer_model": result.model}
        log.info("review verdict", extra={"attempt": state["attempts"], "passed": verdict.passed,
                                          "findings": len(verdict.findings),
                                          "blocking": len(verdict.blocking_findings)})
        if verdict.passed:
            return Command(goto="approve" if settings.require_approval else "publish", update=update)
        if state["attempts"] >= settings.max_attempts:
            raise ReviewRejectedError(_rejection_message(verdict, state["attempts"]))
        update["revision"] = Revision(state["change"], format_findings(verdict))
        return Command(goto="code", update=update)

    def approve(state: RunState) -> Command[Literal["publish", "__end__"]]:
        """Pause for a human decision (human-in-the-loop interrupt point).

        ``interrupt`` saves the state and stops the run. ``resume`` restarts
        this node with the human's answer: ``{"approved": True | False}``.
        Nothing before ``interrupt`` may have side effects, because the node
        runs again from the top on resume.
        """
        decision = interrupt({"run_id": state["run_id"], "ticket": state["ticket_key"],
                              "summary": state["change"].summary,
                              "files": [f"{f.action} {f.path}" for f in state["change"].files]})
        if isinstance(decision, dict) and decision.get("approved") is True:
            log.info("change approved by human")
            return Command(goto="publish")
        log.info("change rejected by human")
        return Command(goto="__end__", update={"human_rejected": True})

    def publish(state: RunState) -> Command[Literal["__end__"]]:
        """Commit the change to the agent branch and open the pull request."""
        snapshot, change = state["snapshot"], state["change"]
        commit_sha = repo.publish(snapshot, state["branch"], change)
        pr = repo.open_pull(title=change.pr_title, body=pr_body(state), head=state["branch"],
                            base=snapshot.default_branch)
        return Command(goto="__end__", update={"pr": pr, "commit_sha": commit_sha})

    graph = StateGraph(RunState)
    graph.add_node("prepare", prepare)
    graph.add_node("code", code)
    graph.add_node("validate", validate)
    graph.add_node("verify", verify)
    graph.add_node("review", review)
    graph.add_node("approve", approve)
    graph.add_node("publish", publish)
    graph.add_edge(START, "prepare")
    return graph.compile(checkpointer=checkpointer)


def format_findings(verdict: ReviewVerdict) -> str:
    """Render a verdict as feedback text: the summary, then one line per finding.

    Example::

        Reviewer summary: Misses the empty-list case.
        - [blocker] bms/protection.py: raise ValueError for an empty list.
    """
    lines = [f"Reviewer summary: {verdict.summary}"]
    lines += [f"- [{f.severity}] {f.path or '(whole change)'}: {f.message}" for f in verdict.findings]
    return "\n".join(lines)


def _rejection_message(verdict: ReviewVerdict, attempts: int) -> str:
    """Build the ``ReviewRejectedError`` message (posted on the ticket as Markdown)."""
    findings = "\n".join(f"- **{f.severity}** `{f.path or 'whole change'}`: {f.message}"
                         for f in verdict.blocking_findings)
    return (f"The reviewer did not approve the change after {attempts} attempt(s).\n\n"
            f"{verdict.summary}\n\nUnresolved findings:\n{findings}")


CHECK_OUTPUT_FEEDBACK = 3000
"""Characters of each failing check's output sent back to the coder (the end of the output)."""


def _choose_checks(repo: RepoHost, base: BaseRef) -> list[CheckSpec]:
    """Choose the checks from the default branch: ``.autoforge.toml`` if present, else detection.

    Raises:
        ChecksConfigError: If ``.autoforge.toml`` is invalid.
    """
    files = repo.list_files(base)
    wanted = [f for f in files if f.path in (CHECKS_CONFIG, "pyproject.toml") and f.mode != "120000"]
    contents = {path: data.decode("utf-8", errors="replace") for path, data in repo.read_files(base, wanted).items()}
    return checks_for([f.path for f in files], config=contents.get(CHECKS_CONFIG),
                      pyproject=contents.get("pyproject.toml"))


def format_check_failures(report: VerificationReport) -> str:
    """Render the checks a change broke as coder feedback: name, command, exit status, end of output."""
    lines = ["Repository checks failed in the sandbox (the change was not accepted). "
             "These checks pass on the base commit, so the change broke them; fix the cause:"]
    for c in report.new_failures:
        lines.append(f"\n### {c.name} ({_status_text(c)})\n$ {c.command}\n{c.output[-CHECK_OUTPUT_FEEDBACK:]}")
    return "\n".join(lines)


def _status_text(check: CheckResult) -> str:
    """E.g. ``"exit code 2"``, ``"timed out"`` or ``"could not run"``."""
    if check.status == "timeout":
        return "timed out"
    if check.status == "error":
        return "could not run"
    return f"exit code {check.exit_code}"


def _checks_failed_message(report: VerificationReport, attempts: int) -> str:
    """Build the ``ChecksFailedError`` message (posted on the ticket as Markdown)."""
    parts = [f"The change still broke repository checks after {attempts} attempt(s)."]
    for c in report.new_failures:
        output = c.output[-1500:].replace("`" * 3, "'" * 3)
        parts.append(f"**{c.name}** ({_status_text(c)}): `{c.command}`\n```\n{output}\n```")
    return "\n\n".join(parts)
