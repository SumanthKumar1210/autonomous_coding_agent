"""GitHub Issues as a `ports.TicketSource`. Status is tracked with `<prefix>:<status>` labels."""

from __future__ import annotations

from autoforge.domain.errors import TicketError
from autoforge.domain.models import Ticket, TicketStatus
from autoforge.integrations.github.client import GitHubClient


class GitHubIssuesSource:
    def __init__(self, github: GitHubClient, label_prefix: str = "autoforge") -> None:
        self._gh = github
        self._label_prefix = label_prefix

    def get_ticket(self, key: str) -> Ticket:
        number = _issue_number(key)
        issue = self._gh.get_issue(number)
        if "pull_request" in issue:
            raise TicketError(f"#{number} is a pull request, not an issue")
        if issue.get("state") != "open":
            raise TicketError(f"Issue #{number} is not open")
        comments = [c.get("body") or "" for c in self._gh.list_issue_comments(number)]
        return Ticket(
            key=str(number),
            title=issue.get("title") or "",
            body=issue.get("body") or "",
            url=issue.get("html_url") or "",
            labels=[label["name"] for label in issue.get("labels", [])],
            comments=comments,
        )

    def comment(self, key: str, body: str) -> None:
        self._gh.create_issue_comment(_issue_number(key), body)

    def set_status(self, key: str, status: TicketStatus) -> None:
        number = _issue_number(key)
        for other in TicketStatus:
            if other is not status:
                self._gh.remove_issue_label(number, self._label(other))
        self._gh.add_issue_labels(number, [self._label(status)])

    def _label(self, status: TicketStatus) -> str:
        return f"{self._label_prefix}:{status.value}"


def _issue_number(key: str) -> int:
    raw = key.lstrip("#")
    if not raw.isdigit():
        raise TicketError(f"Invalid GitHub issue key: {key!r}")
    return int(raw)
