"""Scripted stand-ins for the LLM agents: FakeCoder and FakeReviewer (no network, no API keys)."""

from __future__ import annotations

from autoforge.domain.models import (
    CodeChange,
    CoderResult,
    FileChange,
    RepoSnapshot,
    ReviewerResult,
    ReviewFinding,
    ReviewVerdict,
    Revision,
    Ticket,
    TokenUsage,
)

GOOD_FILES = [FileChange(path="src/temperature.py", action="create", content="def c_to_f(c):\n    return c * 9 / 5 + 32")]


class FakeCoder:
    """Returns a scripted change per attempt (or raises), and records every call.

    ``attempts`` lists the files to return on attempt 1, 2, ...; the last entry
    repeats once the list runs out.
    """

    def __init__(self, files: list[FileChange] | None = None, *, attempts: list[list[FileChange]] | None = None,
                 error: Exception | None = None) -> None:
        """Configure one change for every attempt (``files``), one per attempt (``attempts``), or an ``error``."""
        self.attempts = attempts or [files if files is not None else GOOD_FILES]
        self.error = error
        self.calls: list[tuple[Ticket, RepoSnapshot, Revision | None]] = []

    @property
    def seen(self) -> tuple[Ticket, RepoSnapshot]:
        """Ticket and snapshot passed on the first call."""
        ticket, snapshot, _ = self.calls[0]
        return ticket, snapshot

    def generate(self, ticket: Ticket, snapshot: RepoSnapshot, revision: Revision | None = None) -> CoderResult:
        """Record the inputs and return the scripted change for this attempt (or raise)."""
        self.calls.append((ticket, snapshot, revision))
        if self.error:
            raise self.error
        files = self.attempts[min(len(self.calls), len(self.attempts)) - 1]
        change = CodeChange(summary="Adds conversion.", commit_message="Add temperature conversion",
                            pr_title="Add temperature conversion", pr_body="## Summary\nAdds it.", files=files)
        return CoderResult(change=change, usage=TokenUsage(100, 50), model="coder-model")


def approve(*minor: str) -> ReviewVerdict:
    """An approving verdict, optionally with non-blocking minor findings."""
    return ReviewVerdict(approved=True, summary="Looks good.",
                         findings=[ReviewFinding(severity="minor", path="src/temperature.py", message=m) for m in minor])


def reject(message: str = "Handles no invalid input.", severity: str = "blocker") -> ReviewVerdict:
    """A rejecting verdict with one finding of the given severity."""
    return ReviewVerdict(approved=False, summary="Needs work.",
                         findings=[ReviewFinding(severity=severity, path="src/temperature.py",  # type: ignore[arg-type]
                                                 message=message)])


class FakeReviewer:
    """Returns scripted verdicts in order (the last one repeats), and records every change it saw."""

    def __init__(self, *verdicts: ReviewVerdict, error: Exception | None = None) -> None:
        """Configure the verdicts to return (default: always approve), or an ``error`` to raise."""
        self.verdicts = list(verdicts) or [approve()]
        self.error = error
        self.reviewed: list[CodeChange] = []

    def review(self, ticket: Ticket, snapshot: RepoSnapshot, change: CodeChange) -> ReviewerResult:
        """Record the change and return the next scripted verdict (or raise)."""
        self.reviewed.append(change)
        if self.error:
            raise self.error
        verdict = self.verdicts[min(len(self.reviewed), len(self.verdicts)) - 1]
        return ReviewerResult(verdict=verdict, usage=TokenUsage(40, 10), model="reviewer-model")
