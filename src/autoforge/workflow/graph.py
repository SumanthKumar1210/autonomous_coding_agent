"""The agent graph (LangGraph): coder <-> reviewer loop, approval gate, publish.

::

    prepare ──► code ──► validate ──► review ──► [approve] ──► publish ──► END
                 ▲           │            │          │
                 └─ policy ──┘            │          └─ human rejects ──► END
                 └──── review findings ───┘

Nodes (each one step, each returns a ``Command`` naming the next node):

- **prepare** - build the repository snapshot for the agents.
- **code** - the coder proposes a change (on a retry: with ``Revision`` feedback).
- **validate** - ``domain.policy`` checks the change. A violation goes back to
  the coder as feedback (it is a correctable agent mistake), until attempts run out.
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
from autoforge.domain.errors import ChangeValidationError, ReviewRejectedError
from autoforge.domain.models import (
    BaseRef,
    CodeChange,
    FileChange,
    PullRequest,
    RepoSnapshot,
    ReviewFinding,
    ReviewVerdict,
    Revision,
    Ticket,
    TokenUsage,
)
from autoforge.domain.policy import validate_change
from autoforge.ports import Coder, RepoHost, Reviewer

log = logging.getLogger(__name__)

CHECKPOINT_TYPES: tuple[type, ...] = (
    Ticket, BaseRef, RepoSnapshot, CodeChange, FileChange, ReviewVerdict, ReviewFinding, Revision, TokenUsage,
    PullRequest,
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
        change: The coder's latest proposed change.
        revision: Feedback for the coder's next attempt (``None`` once addressed).
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
    change: CodeChange
    revision: Revision | None
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
) -> AgentGraph:
    """Build and compile the agent graph.

    Args:
        repo: Repository host (read context, publish, open PR).
        coder: The coder agent.
        reviewer: The reviewer agent.
        checkpointer: Where run state is saved after every node.
        settings: Attempt limit, approval gate, context budget.
        pr_body: Builds the PR description from the final state.

    Returns:
        The compiled graph. Run it with
        ``graph.invoke(state, {"configurable": {"thread_id": run_id}})``.
    """

    def prepare(state: RunState) -> Command[Literal["code"]]:
        """Build the repository snapshot both agents will see."""
        snapshot = build_snapshot(repo, max_context_bytes=settings.max_context_bytes,
                                  max_file_bytes=settings.max_file_bytes)
        return Command(goto="code", update={"snapshot": snapshot, "attempts": 0, "usage": TokenUsage(),
                                            "revision": None})

    def code(state: RunState) -> Command[Literal["validate"]]:
        """Ask the coder for a change (with feedback, if this is a revision)."""
        attempt = state["attempts"] + 1
        log.info("coder attempt", extra={"attempt": attempt, "revision": state.get("revision") is not None})
        result = coder.generate(state["ticket"], state["snapshot"], state.get("revision"))
        return Command(goto="validate", update={
            "change": result.change, "attempts": attempt, "usage": state["usage"] + result.usage,
            "coder_model": result.model, "revision": None,
        })

    def validate(state: RunState) -> Command[Literal["review", "code"]]:
        """Check the change against the change policy; send violations back to the coder."""
        try:
            validate_change(state["change"], state["snapshot"])
        except ChangeValidationError as exc:
            log.warning("change rejected by policy", extra={"attempt": state["attempts"], "error": str(exc)})
            if state["attempts"] >= settings.max_attempts:
                raise
            feedback = f"Change policy violation (the change was not accepted): {exc}"
            return Command(goto="code", update={"revision": Revision(state["change"], feedback)})
        return Command(goto="review")

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
