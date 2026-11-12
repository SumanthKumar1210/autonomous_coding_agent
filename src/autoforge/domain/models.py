"""Core data models shared across all layers.

There are three groups of models:

1. **Tickets** - the unit of work and its status.
2. **Repository** - what AutoForge knows about the target repo for one run.
3. **Agent output** - the structured change the LLM coder returns. These are
   pydantic models because their JSON schema is sent to OpenAI as the required
   response format ("structured outputs").

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


@dataclass(frozen=True)
class TokenUsage:
    """LLM token counts for one call, used for cost tracking.

    Attributes:
        input_tokens: Tokens sent to the model (prompt + repository context).
        output_tokens: Tokens the model generated (including reasoning).
    """

    input_tokens: int
    output_tokens: int


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
