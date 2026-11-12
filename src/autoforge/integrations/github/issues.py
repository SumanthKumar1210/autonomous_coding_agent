"""GitHub Issues as a ``ports.TicketSource``.

A GitHub issue plays the role of a Jira ticket. Progress is shown with labels:
``autoforge:in-progress``, ``autoforge:pr-opened`` or ``autoforge:failed``
(the prefix is configurable via ``TRIGGER_LABEL``).
"""

from __future__ import annotations

from autoforge.domain.errors import TicketError
from autoforge.domain.models import Ticket, TicketStatus
from autoforge.integrations.github.client import GitHubClient


class GitHubIssuesSource:
    """Reads GitHub issues as ``Ticket`` objects and reports status back on them.

    Implements the ``ports.TicketSource`` interface.
    """

    def __init__(self, github: GitHubClient, label_prefix: str = "autoforge") -> None:
        """Create the source.

        Args:
            github: REST client for the target repository.
            label_prefix: Prefix for status labels, e.g. ``"autoforge"`` ->
                ``"autoforge:pr-opened"``.
        """
        self._gh = github
        self._label_prefix = label_prefix

    def get_ticket(self, key: str) -> Ticket:
        """Load issue ``key`` (e.g. ``"12"`` or ``"#12"``) with its comments.

        Raises:
            TicketError: If the key is invalid, the number belongs to a pull
                request, or the issue is closed.
            GitHubError: If the issue does not exist (404) or GitHub fails.
        """
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
        """Post a Markdown comment on issue ``key``."""
        self._gh.create_issue_comment(_issue_number(key), body)

    def set_status(self, key: str, status: TicketStatus) -> None:
        """Show ``status`` on the issue as a label, removing the other status labels.

        Example: ``set_status("7", TicketStatus.PR_OPENED)`` leaves exactly
        ``autoforge:pr-opened`` among AutoForge's labels on issue #7.
        """
        number = _issue_number(key)
        for other in TicketStatus:
            if other is not status:
                self._gh.remove_issue_label(number, self._label(other))
        self._gh.add_issue_labels(number, [self._label(status)])

    def _label(self, status: TicketStatus) -> str:
        """Return the label name for a status, e.g. ``"autoforge:failed"``."""
        return f"{self._label_prefix}:{status.value}"


def _issue_number(key: str) -> int:
    """Convert a ticket key like ``"12"`` or ``"#12"`` to the issue number ``12``.

    Raises:
        TicketError: If the key is not a number.
    """
    raw = key.lstrip("#")
    if not raw.isdigit():
        raise TicketError(f"Invalid GitHub issue key: {key!r}")
    return int(raw)
