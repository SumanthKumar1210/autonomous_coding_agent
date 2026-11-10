"""Core domain models shared across all layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

# ----------------------------------------------------------------- tickets


@dataclass(frozen=True)
class Ticket:
    """A unit of work, independent of its source (GitHub Issues now, Jira later)."""

    key: str  # source-specific identifier, e.g. "12" for GitHub issue #12
    title: str
    body: str
    url: str
    labels: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)


class TicketStatus(str, Enum):
    IN_PROGRESS = "in-progress"
    PR_OPENED = "pr-opened"
    FAILED = "failed"


# -------------------------------------------------------------- repository


@dataclass(frozen=True)
class BaseRef:
    """The commit on the default branch that a change is built on top of."""

    branch: str
    commit_sha: str
    tree_sha: str


@dataclass(frozen=True)
class RepoFile:
    path: str
    mode: str  # git file mode, e.g. "100644"
    size: int
    blob_id: str


@dataclass(frozen=True)
class RepoSnapshot:
    """What the agents know about the repository for one run."""

    base: BaseRef
    modes: dict[str, str]  # every file path in the repo -> git mode
    files: dict[str, str] = field(default_factory=dict)  # path -> content, for files shown to the LLM

    @property
    def default_branch(self) -> str:
        return self.base.branch


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str


# --------------------------------------------- agent output (LLM structured output)
# OpenAI strict JSON schema: every field is required, no defaults.


class FileChange(BaseModel):
    path: str = Field(description="Repository-relative POSIX path, e.g. 'src/pkg/module.py'.")
    action: Literal["create", "modify", "delete"]
    content: str = Field(description="Full new file content for create/modify; empty string for delete.")


class CodeChange(BaseModel):
    summary: str = Field(description="One-paragraph summary of what the change does.")
    commit_message: str = Field(description="Conventional, imperative commit message (subject line + optional body).")
    pr_title: str = Field(description="Short pull request title.")
    pr_body: str = Field(
        description="Markdown PR description: what and why, how to verify, assumptions and open questions."
    )
    files: list[FileChange]


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class CoderResult:
    change: CodeChange
    usage: TokenUsage
    model: str
