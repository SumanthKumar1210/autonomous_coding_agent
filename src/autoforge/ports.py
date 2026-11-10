"""Ports: the interfaces the workflow depends on. Integrations implement them.

The workflow never imports an integration directly; `app/wiring.py` plugs
concrete implementations in. This keeps the core testable and lets Jira (or
another git host) be added without touching agents or workflow.
"""

from __future__ import annotations

from typing import Protocol

from autoforge.domain.models import (
    BaseRef,
    CodeChange,
    CoderResult,
    PullRequest,
    RepoFile,
    RepoSnapshot,
    Ticket,
    TicketStatus,
)


class TicketSource(Protocol):
    """Where work comes from and where progress is reported (GitHub Issues; Jira later)."""

    def get_ticket(self, key: str) -> Ticket: ...
    def comment(self, key: str, body: str) -> None: ...
    def set_status(self, key: str, status: TicketStatus) -> None: ...


class RepoHost(Protocol):
    """The target code repository. Deliberately has no merge/approve operation: PR-only."""

    @property
    def name(self) -> str: ...
    def get_base(self) -> BaseRef: ...
    def list_files(self, base: BaseRef) -> list[RepoFile]: ...
    def read_file(self, file: RepoFile) -> bytes: ...
    def find_open_pull(self, branch: str) -> PullRequest | None: ...
    def publish(self, snapshot: RepoSnapshot, branch: str, change: CodeChange) -> str: ...
    def open_pull(self, *, title: str, body: str, head: str, base: str) -> PullRequest: ...


class Coder(Protocol):
    """Agent that turns a ticket + repository snapshot into a code change."""

    def generate(self, ticket: Ticket, snapshot: RepoSnapshot) -> CoderResult: ...
