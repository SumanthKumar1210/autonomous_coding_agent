"""Core data models shared across all layers.

There are four groups of models:

1. **Tickets** - the unit of work and its status.
2. **Repository** - what AutoForge knows about the target repo for one run.
3. **Agent output** - the structured change the coder returns and the verdict
   the reviewer returns. These are pydantic models because their JSON schema is
   sent to OpenAI as the required response format ("structured outputs").
4. **Agent bookkeeping** - token usage, results, and the revision feedback that
   goes from the reviewer back to the coder.

Plain ``dataclass(frozen=True)`` is used for everything else: immutable,
simple, and no validation overhead where none is needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

# ----------------------------------------------------------------- tickets


@dataclass(frozen=True)
class Ticket:
    """A unit of work, independent of where it came from.

    Today tickets come from GitHub Issues; later Jira can produce the same
    object, so the agents and workflow never need to know the source.

    Attributes:
        key: Source-specific identifier, e.g. ``"12"`` for GitHub issue #12.
        title: Short summary of the requested change.
        body: Full description, ideally including acceptance criteria.
        url: Link to the ticket in its source system (for humans).
        labels: Labels/tags attached to the ticket.
        comments: Discussion comments, oldest first. They often hold clarifications.
    """

    key: str
    title: str
    body: str
    url: str
    labels: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)


class TicketStatus(str, Enum):
    """Progress states AutoForge reports back on a ticket.

    On GitHub these become labels such as ``autoforge:in-progress``.
    """

    IN_PROGRESS = "in-progress"
    AWAITING_APPROVAL = "awaiting-approval"
    PR_OPENED = "pr-opened"
    FAILED = "failed"


# -------------------------------------------------------------- repository


@dataclass(frozen=True)
class BaseRef:
    """The exact commit on the default branch that a change is built on top of.

    Pinning the commit (not just the branch name) guarantees the agent's change
    is applied to the same code it was shown, even if someone pushes meanwhile.

    Attributes:
        branch: Default branch name, e.g. ``"main"``.
        commit_sha: SHA of the branch head at the start of the run.
        tree_sha: SHA of that commit's file tree (needed to build a new commit).
    """

    branch: str
    commit_sha: str
    tree_sha: str


@dataclass(frozen=True)
class RepoFile:
    """One file in the repository tree (metadata only, not the content).

    Attributes:
        path: Repository-relative POSIX path, e.g. ``"bms/protection.py"``.
        mode: Git file mode: ``"100644"`` normal, ``"100755"`` executable,
            ``"120000"`` symlink.
        size: Size in bytes.
        blob_id: Git blob SHA, used to download the content when needed.
    """

    path: str
    mode: str
    size: int
    blob_id: str


@dataclass(frozen=True)
class RepoSnapshot:
    """Everything the agents know about the repository for one run.

    Attributes:
        base: The commit the change will be built on.
        modes: Every file path in the repo mapped to its git mode. Used to know
            which files exist and to preserve modes when editing.
        files: Content of the files actually shown to the LLM (a budgeted
            subset). The agent may only *modify* files listed here.
    """

    base: BaseRef
    modes: dict[str, str]
    files: dict[str, str] = field(default_factory=dict)

    @property
    def default_branch(self) -> str:
        """Name of the branch PRs are opened against (e.g. ``"main"``)."""
        return self.base.branch


@dataclass(frozen=True)
class PullRequest:
    """A pull request opened (or found) on the repository host.

    Attributes:
        number: PR number, e.g. ``42``.
        url: Browser link to the PR.
    """

    number: int
    url: str


# --------------------------------------------- agent output (LLM structured output)
# OpenAI strict JSON schema rules: every field is required and has no default.
#
# NOTE for developers: pydantic copies the class docstrings and Field
# descriptions below into the JSON schema sent to OpenAI, so they are part of
# the prompt. Keep them short and written for the model; put developer notes in
# comments like this one.

# FileChange: one file operation. `content` is the whole file (not a diff) for
# create/modify and "" for delete. domain.policy validates every FileChange.


class FileChange(BaseModel):
    """One file operation in the change."""

    path: str = Field(description="Repository-relative POSIX path, e.g. 'src/pkg/module.py'.")
    action: Literal["create", "modify", "delete"]
    content: str = Field(description="Full new file content for create/modify; empty string for delete.")


# CodeChange: the complete change for one ticket. `summary` is posted on the
# ticket, commit_message/pr_title/pr_body are used for the commit and PR, and
# `files` lists the operations.


class CodeChange(BaseModel):
    """The complete code change that implements the ticket."""

    summary: str = Field(description="One-paragraph summary of what the change does.")
    commit_message: str = Field(description="Conventional, imperative commit message (subject line + optional body).")
    pr_title: str = Field(description="Short pull request title.")
    pr_body: str = Field(
        description="Markdown PR description: what and why, how to verify, assumptions and open questions."
    )
    files: list[FileChange]


# ReviewFinding / ReviewVerdict: the reviewer agent's structured output. The
# reviewer's own `approved` flag is not trusted alone: `ReviewVerdict.passed`
# also requires that no blocker or major finding is listed.


class ReviewFinding(BaseModel):
    """One problem found in the proposed change."""

    severity: Literal["blocker", "major", "minor"] = Field(
        description="blocker: wrong, unsafe or misses an acceptance criterion; major: must fix before merge; "
                    "minor: optional improvement."
    )
    path: str = Field(description="File the finding is about, or empty string if it concerns the change as a whole.")
    message: str = Field(description="What is wrong and what the fix should be, specific enough to act on.")


class ReviewVerdict(BaseModel):
    """The reviewer's decision on the proposed change."""

    approved: bool = Field(description="True only if the change can be merged as is (no blocker or major findings).")
    summary: str = Field(description="One-paragraph assessment of the change.")
    findings: list[ReviewFinding]

    @property
    def passed(self) -> bool:
        """True if the reviewer approved *and* listed no blocker or major finding."""
        return self.approved and not self.blocking_findings

    @property
    def blocking_findings(self) -> list[ReviewFinding]:
        """Findings that must be fixed before the change can be published."""
        return [f for f in self.findings if f.severity in ("blocker", "major")]


# ----------------------------------------------------------- agent bookkeeping


@dataclass(frozen=True)
class TokenUsage:
    """LLM token counts, used for cost tracking.

    Supports ``+`` so a run can total the usage of all its LLM calls.

    Attributes:
        input_tokens: Tokens sent to the model (prompt + repository context).
        output_tokens: Tokens the model generated (including reasoning).
    """

    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        """Return the element-wise sum of two usages."""
        return TokenUsage(self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens)


@dataclass(frozen=True)
class CoderResult:
    """What a coder agent returns: the change plus bookkeeping about how it was made.

    Attributes:
        change: The proposed code change.
        usage: Token usage of the LLM call.
        model: Model ID that produced the change (recorded in the PR footer).
    """

    change: CodeChange
    usage: TokenUsage
    model: str


@dataclass(frozen=True)
class ReviewerResult:
    """What a reviewer agent returns: the verdict plus bookkeeping.

    Attributes:
        verdict: Approve / reject decision with findings.
        usage: Token usage of the LLM call.
        model: Model ID that produced the verdict.
    """

    verdict: ReviewVerdict
    usage: TokenUsage
    model: str


@dataclass(frozen=True)
class Revision:
    """Feedback sent back to the coder when its previous attempt was not accepted.

    Produced either by the reviewer (findings) or by ``domain.policy`` (a
    validation error), and consumed by the coder on its next attempt.

    Attributes:
        previous: The change the coder proposed last time.
        feedback: What must be fixed, as plain text (one item per line).
    """

    previous: CodeChange
    feedback: str


# ------------------------------------------------------------- triggers & jobs


class TriggerKind(str, Enum):
    """What a trigger asks AutoForge to do.

    - ``RUN``: implement the ticket (e.g. the ``autoforge`` label was added).
    - ``APPROVE`` / ``REJECT``: decide a run paused at the approval gate
      (e.g. an ``/autoforge approve`` comment).
    """

    RUN = "run"
    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True)
class Trigger:
    """A request to act on a ticket, independent of where it came from.

    Produced by the webhook (from a GitHub event) or by the CLI.

    Attributes:
        kind: Run, approve or reject.
        ticket_key: Ticket the request is about, e.g. ``"12"``.
        delivery_id: Unique ID of the request. For webhooks this is GitHub's
            ``X-GitHub-Delivery`` header, so a redelivered event is recognised
            and ignored.
        actor: Login of the user who caused it (checked for write access), or
            ``None`` for the trusted local CLI.
    """

    kind: TriggerKind
    ticket_key: str
    delivery_id: str
    actor: str | None = None


class JobStatus(str, Enum):
    """Lifecycle of a queued job: ``queued`` -> ``running`` -> ``done`` / ``failed``."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class Job:
    """A trigger stored in the job queue, plus what became of it.

    Attributes:
        id: Queue position (auto-increment).
        trigger: The request.
        status: Where the job is in its lifecycle.
        run_id: The workflow run the job started or resumed, once known.
        outcome: Result, e.g. ``"pr_opened"``, ``"awaiting_approval"``,
            ``"unauthorized"`` or ``"skipped"``.
        error: Failure reason, if any.
    """

    id: int
    trigger: Trigger
    status: JobStatus
    run_id: str | None = None
    outcome: str | None = None
    error: str | None = None
