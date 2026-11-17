"""Run eval cases through the production agent graph and grade the results.

Three entry points, from cheapest to most expensive:

- ``context_case``: build the context for a case and score it. Deterministic,
  no LLM, no sandbox; runs in CI.
- ``validate_case``: prove a case is sound. The hidden checks must fail on the
  untouched fixture and pass with the reference solution. Needs the sandbox,
  but no LLM.
- ``run_case``: run the agents on a case in one ``Variant`` and grade the final
  change. Spends LLM tokens.

A variant switches parts of the loop off, so the difference between two
variants measures what that part adds. For example, ``full`` against
``no-review`` measures reviewer lift.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from autoforge.agents.context import build_snapshot
from autoforge.domain.errors import AgentError, ChangeValidationError, ChecksFailedError, ReviewRejectedError
from autoforge.domain.models import (
    CodeChange,
    ContextFile,
    RepoSnapshot,
    ReviewerResult,
    ReviewVerdict,
    Ticket,
    TokenUsage,
)
from autoforge.evals.grading import (
    ChangeScore,
    ContextScore,
    grade_change,
    repo_checks,
    run_checks,
    score_context,
    solution_change,
)
from autoforge.evals.local_repo import BRANCH_PREFIX, LocalRepoHost
from autoforge.evals.suite import EvalCase
from autoforge.observability import run_context
from autoforge.ports import Coder, Reviewer, Verifier
from autoforge.workflow.graph import GraphSettings, RunState, build_graph, checkpoint_serializer

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Variant:
    """Which parts of the agent loop run.

    Attributes:
        name: Short ID used on the command line and in reports.
        description: One line for the report.
        review: Run the reviewer agent (otherwise every change is approved as is).
        feedback: Allow revisions: policy violations and broken checks (and, with
            ``review``, reviewer findings) go back to the coder, up to ``max_attempts``.
            Without it the coder gets exactly one attempt and no checks run in the loop.
    """

    name: str
    description: str
    review: bool
    feedback: bool


VARIANTS: dict[str, Variant] = {v.name: v for v in (
    Variant("single", "one coder attempt; no checks or reviewer in the loop", review=False, feedback=False),
    Variant("no-review", "coder with policy and check feedback; no reviewer", review=False, feedback=True),
    Variant("full", "production loop: coder with policy, check and reviewer feedback", review=True, feedback=True),
)}
"""Every variant, by name."""


@dataclass(frozen=True)
class EvalAgents:
    """What a run needs from outside the harness (real OpenAI agents and sandbox, or fakes in tests).

    Attributes:
        coder: The coder agent.
        reviewer: The reviewer agent (used only by variants with ``review``).
        verifier: The verification sandbox; ``None`` means checks neither run in the loop nor in grading.
    """

    coder: Coder
    reviewer: Reviewer
    verifier: Verifier | None


@dataclass(frozen=True)
class EvalBudget:
    """Run limits, as in production (``WorkflowConfig``).

    Attributes:
        max_context_bytes: Total size of file contents sent to the LLMs.
        max_file_bytes: Largest file sent in full.
        max_attempts: Coder attempts for variants with ``feedback``.
    """

    max_context_bytes: int = 60_000
    max_file_bytes: int = 20_000
    max_attempts: int = 3


@dataclass(frozen=True)
class CaseRun:
    """The result of one case in one variant.

    Attributes:
        case_id: The case.
        variant: Variant name.
        repeat: Repetition number (1-based).
        run_id: ID tagging this run's log lines.
        outcome: ``completed`` (the graph published), ``policy_failed``, ``checks_failed``,
            ``review_rejected``, ``agent_error`` or ``error``.
        error: The exception message for anything other than ``completed``.
        attempts: Coder attempts made.
        usage: Tokens of every LLM call in the run.
        seconds: Wall-clock time of the agent run (grading excluded).
        coder_model: Model ID of the coder.
        reviewer_model: Model ID of the reviewer (``""`` without a reviewer).
        context: Context score (``None`` if the run failed before building the context).
        change: Grades of the final change (``None`` if the coder produced none).
    """

    case_id: str
    variant: str
    repeat: int
    run_id: str
    outcome: str
    error: str
    attempts: int
    usage: TokenUsage
    seconds: float
    coder_model: str
    reviewer_model: str
    context: ContextScore | None
    change: ChangeScore | None

    @property
    def resolved(self) -> bool:
        """True if the final change passes policy and every repository and hidden check."""
        return self.change is not None and self.change.resolved


class ApproveAll:
    """Stand-in reviewer for variants without review: approves every change, costs nothing."""

    def review(self, ticket: Ticket, snapshot: RepoSnapshot, change: CodeChange) -> ReviewerResult:
        """Approve ``change`` without looking at it."""
        verdict = ReviewVerdict(approved=True, summary="No reviewer in this eval variant.", findings=[])
        return ReviewerResult(verdict=verdict, usage=TokenUsage(), model="")


def context_case(case: EvalCase, budget: EvalBudget) -> tuple[RepoSnapshot, ContextScore]:
    """Build the context for ``case`` exactly as a production run would, and score it."""
    snapshot = build_snapshot(LocalRepoHost(case.repo_dir), case.ticket, max_context_bytes=budget.max_context_bytes,
                              max_file_bytes=budget.max_file_bytes)
    return snapshot, score_context(case, snapshot)


def run_case(case: EvalCase, variant: Variant, agents: EvalAgents, budget: EvalBudget, *,
             repeat: int = 1) -> CaseRun:
    """Run the agent graph on ``case`` in ``variant`` and grade the final change.

    Expected failures (policy, checks, review, agent errors) are part of the
    measurement and end up in ``CaseRun.outcome``; the last change is still
    graded. Never raises for a failed run.
    """
    repo = LocalRepoHost(case.repo_dir)
    in_loop_verifier = agents.verifier if variant.feedback else None
    graph = build_graph(
        repo=repo, coder=agents.coder, reviewer=agents.reviewer if variant.review else ApproveAll(),
        checkpointer=InMemorySaver(serde=checkpoint_serializer()),
        settings=GraphSettings(max_context_bytes=budget.max_context_bytes, max_file_bytes=budget.max_file_bytes,
                               max_attempts=budget.max_attempts if variant.feedback else 1, require_approval=False),
        pr_body=lambda state: "", verifier=in_loop_verifier,
    )
    run_id = f"eval-{uuid.uuid4().hex[:10]}"
    cfg: RunnableConfig = {"configurable": {"thread_id": run_id}}
    initial: RunState = {"run_id": run_id, "ticket_key": case.id, "branch": f"{BRANCH_PREFIX}eval-{case.id}",
                         "ticket": case.ticket}
    outcome, error = "completed", ""
    with run_context(run_id):
        log.info("eval run started", extra={"case": case.id, "variant": variant.name, "repeat": repeat})
        start = time.monotonic()
        try:
            graph.invoke(initial, cfg)
        except Exception as exc:  # every failure is a result to grade, not a crash
            outcome, error = _outcome_of(exc), f"{type(exc).__name__}: {exc}"
            if outcome == "error":
                log.exception("eval run crashed", extra={"case": case.id})
        seconds = round(time.monotonic() - start, 2)
        state: RunState = graph.get_state(cfg).values  # type: ignore[assignment]
        snapshot, change = state.get("snapshot"), state.get("change")
        context = score_context(case, snapshot) if snapshot is not None else None
        score = None
        if snapshot is not None and change is not None:
            archive = repo.download_archive(snapshot.base) if agents.verifier is not None else None
            score = grade_change(case, snapshot, change, repo.files, verifier=agents.verifier, archive=archive)
        result = CaseRun(case_id=case.id, variant=variant.name, repeat=repeat, run_id=run_id, outcome=outcome,
                         error=error[:2000], attempts=state.get("attempts", 0), usage=state.get("usage", TokenUsage()),
                         seconds=seconds, coder_model=state.get("coder_model", ""),
                         reviewer_model=state.get("reviewer_model", ""), context=context, change=score)
        log.info("eval run graded", extra={"case": case.id, "variant": variant.name, "outcome": outcome,
                                           "resolved": result.resolved, "attempts": result.attempts,
                                           "input_tokens": result.usage.input_tokens,
                                           "output_tokens": result.usage.output_tokens})
    return result


def validate_case(case: EvalCase, verifier: Verifier | None) -> list[str]:
    """Check that ``case`` can tell good changes from bad ones; return the problems found (empty: sound).

    Always: the reference solution creates/modifies every expected file,
    touches no protected file and matches every pattern. With a sandbox also:
    on the untouched fixture the repository checks pass and every hidden check
    fails, and with the solution every check passes.
    """
    repo = LocalRepoHost(case.repo_dir)
    base = repo.get_base()
    full_view = RepoSnapshot(base=base, modes={p: "100644" for p in repo.files}, files=dict(repo.files),
                             context=[ContextFile(p, "validation") for p in repo.files])
    solution = solution_change(case, repo.files)
    problems: list[str] = []
    if not case.solution_files:
        problems.append("no reference solution (solution/ is empty)")
    if not case.hidden_checks:
        problems.append("no hidden checks")

    archive = repo.download_archive(base) if verifier is not None else None
    score = grade_change(case, full_view, solution, repo.files, verifier=verifier, archive=archive)
    if score.policy_error:
        problems.append(f"solution violates the policy: {score.policy_error}")
    problems += [f"solution does not change {p}" for p in score.changed_missing]
    problems += [f"solution touches protected {p}" for p in score.unchanged_violations]
    problems += [f"solution does not match {p}" for p in score.patterns_missing]
    if verifier is None or archive is None:
        return problems
    if score.checks_skipped:
        return [*problems, f"solution checks did not run: {score.checks_skipped}"]
    problems += [f"solution fails {c.name} ({c.status}):\n{c.output}" for c in score.checks if not c.passed]

    untouched = CodeChange(summary="", commit_message="", pr_title="", pr_body="", files=[])
    base_checks, skipped = run_checks(case, untouched, repo.files, verifier, archive)
    if skipped:
        return [*problems, f"base checks did not run: {skipped}"]
    repo_names = {c.name for c in repo_checks(repo.files)}
    problems += [f"repository check {c.name} fails on the untouched fixture ({c.status}):\n{c.output}"
                 for c in base_checks if c.name in repo_names and not c.passed]
    problems += [f"{c.name} already passes on the untouched fixture, so it tests nothing"
                 for c in base_checks if c.hidden and c.passed]
    return problems


def _outcome_of(exc: Exception) -> str:
    """Name the kind of failure (subclasses of ``AgentError`` first)."""
    if isinstance(exc, ChangeValidationError):
        return "policy_failed"
    if isinstance(exc, ChecksFailedError):
        return "checks_failed"
    if isinstance(exc, ReviewRejectedError):
        return "review_rejected"
    if isinstance(exc, AgentError):
        return "agent_error"
    return "error"
