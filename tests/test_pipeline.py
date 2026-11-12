"""End-to-end tests of a ticket run: real workflow + real GitHub adapters, against FakeGitHub and FakeCoder."""

from __future__ import annotations

import pytest

from autoforge.domain.errors import TicketError
from autoforge.domain.models import CodeChange, CoderResult, FileChange, RepoSnapshot, Ticket, TokenUsage
from autoforge.integrations.github.issues import GitHubIssuesSource
from autoforge.integrations.github.repo import GitHubRepoHost
from autoforge.workflow.pipeline import Dependencies, RunOutcome, WorkflowConfig, run_ticket

from tests.fake_github import FakeGitHub


class FakeCoder:
    """Stand-in for the OpenAI coder: returns a fixed change (or raises), and records what it was given."""
    def __init__(self, files: list[FileChange] | None = None, error: Exception | None = None) -> None:
        """Configure the files to return, or an ``error`` to raise instead."""
        self.files = files if files is not None else [
            FileChange(path="src/temperature.py", action="create", content="def c_to_f(c):\n    return c * 9 / 5 + 32"),
        ]
        self.error = error
        self.seen: tuple[Ticket, RepoSnapshot] | None = None

    def generate(self, ticket: Ticket, snapshot: RepoSnapshot) -> CoderResult:
        """Record the inputs and return the canned change (or raise the configured error)."""
        self.seen = (ticket, snapshot)
        if self.error:
            raise self.error
        change = CodeChange(summary="Adds conversion.", commit_message="Add temperature conversion",
                            pr_title="Add temperature conversion", pr_body="## Summary\nAdds it.", files=self.files)
        return CoderResult(change=change, usage=TokenUsage(100, 50), model="test-model")


def _run(fake: FakeGitHub, coder: FakeCoder, key: str = "7"):
    """Run the real workflow for ticket ``key`` against the fake GitHub and the given coder."""
    gh = fake.client()
    config = WorkflowConfig()
    deps = Dependencies(tickets=GitHubIssuesSource(gh), repo=GitHubRepoHost(gh, branch_prefix=config.branch_prefix),
                        coder=coder)
    return run_ticket(key, deps, config)


def test_happy_path_opens_pr_and_reports_back() -> None:
    """A valid change opens a PR on the agent branch, leaves main untouched and reports back on the issue."""
    fake = FakeGitHub()
    fake.add_issue(7, title="Add C to F conversion")
    coder = FakeCoder()

    result = _run(fake, coder)

    assert result.outcome is RunOutcome.PR_OPENED
    assert result.pr_url == fake.pulls[0]["html_url"]
    pr = fake.pulls[0]
    assert pr["head"] == "autoforge/issue-7" and pr["base"] == "main"
    assert "Closes #7" in pr["body"] and result.run_id in pr["body"]
    assert fake.refs["autoforge/issue-7"] == "commit-1"
    assert fake.refs["main"] == "commit-base"  # default branch untouched
    assert fake.created_commits[0]["parents"] == ["commit-base"]
    assert fake.created_trees[0]["tree"][0]["content"].endswith("\n")
    assert fake.labels[7] == ["autoforge:pr-opened"]
    assert result.pr_url in fake.comments[7][-1]
    ticket, snapshot = coder.seen  # type: ignore[misc]
    assert ticket.title == "Add C to F conversion"
    assert snapshot.files["src/app.py"] == "print('hi')\n"


def test_existing_open_pr_is_not_duplicated() -> None:
    """Running the same ticket twice does not open a second PR (idempotency)."""
    fake = FakeGitHub()
    fake.add_issue(7)
    _run(fake, FakeCoder())

    second = _run(fake, FakeCoder())

    assert second.outcome is RunOutcome.ALREADY_OPEN
    assert len(fake.pulls) == 1


def test_stale_branch_without_pr_is_reset() -> None:
    """A leftover agent branch without an open PR is reset to the new commit."""
    fake = FakeGitHub()
    fake.add_issue(7)
    fake.refs["autoforge/issue-7"] = "old-commit"

    result = _run(fake, FakeCoder())

    assert result.outcome is RunOutcome.PR_OPENED
    assert fake.refs["autoforge/issue-7"] == "commit-1"
    assert ("PATCH", "/git/refs/heads/autoforge/issue-7") in fake.calls


def test_coder_failure_is_reported_on_ticket() -> None:
    """If the coder fails, the issue gets a 'failed' label and an error comment, and nothing is pushed."""
    fake = FakeGitHub()
    fake.add_issue(7)

    result = _run(fake, FakeCoder(error=RuntimeError("model unavailable")))

    assert result.outcome is RunOutcome.FAILED
    assert "model unavailable" in (result.error or "")
    assert fake.labels[7] == ["autoforge:failed"]
    assert "model unavailable" in fake.comments[7][-1]
    assert not fake.pulls and "autoforge/issue-7" not in fake.refs


def test_invalid_change_never_reaches_github() -> None:
    """A change violating policy (CI workflow edit) is rejected before any write to GitHub."""
    fake = FakeGitHub()
    fake.add_issue(7)
    evil = [FileChange(path=".github/workflows/ci.yml", action="create", content="on: push")]

    result = _run(fake, FakeCoder(files=evil))

    assert result.outcome is RunOutcome.FAILED
    assert not fake.created_trees and not fake.pulls
    assert fake.labels[7] == ["autoforge:failed"]


def test_pull_request_number_is_rejected_cleanly() -> None:
    """Passing a PR number instead of an issue fails cleanly without touching it."""
    fake = FakeGitHub()
    fake.add_issue(7, pull_request={"url": "x"})

    result = _run(fake, FakeCoder())

    assert result.outcome is RunOutcome.FAILED
    assert TicketError.__name__ in (result.error or "") and "pull request" in (result.error or "")
    assert fake.labels[7] == [] and fake.comments[7] == []


def test_empty_repository_is_reported_clearly() -> None:
    """An empty repo (GitHub 409) fails with a clear 'push an initial commit' message."""
    fake = FakeGitHub()
    fake.add_issue(7)
    fake.fail[("GET", r"/git/ref/heads/main")] = 409

    result = _run(fake, FakeCoder())

    assert result.outcome is RunOutcome.FAILED
    assert "push an initial commit" in (result.error or "")
    assert "push an initial commit" in fake.comments[7][-1]


def test_missing_issue_fails_cleanly() -> None:
    """A non-existent issue number returns FAILED instead of crashing."""
    result = _run(FakeGitHub(), FakeCoder(), key="404")

    assert result.outcome is RunOutcome.FAILED
    assert "404" in (result.error or "")
