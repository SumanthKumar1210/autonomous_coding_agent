"""Tests for the GitHub REST client: retry policy, 404 handling, auth header, and no merge capability."""

from __future__ import annotations

import httpx
import pytest

from autoforge.integrations.github.client import GitHubClient, GitHubError


def _client(handler) -> GitHubClient:
    """Create a GitHubClient whose requests go to ``handler`` instead of the network."""
    return GitHubClient("tok", "acme/sandbox", transport=httpx.MockTransport(handler), sleep=lambda _: None)


def test_get_retries_on_5xx_then_succeeds() -> None:
    """A GET is retried on server errors until it succeeds."""
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Fake GitHub endpoint for this test."""
        attempts.append(1)
        return httpx.Response(503) if len(attempts) < 3 else httpx.Response(200, json={"default_branch": "main"})

    assert _client(handler).default_branch() == "main"
    assert len(attempts) == 3


def test_post_is_not_retried_on_5xx() -> None:
    """A POST is not retried on 5xx, since GitHub may already have created the resource."""
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Fake GitHub endpoint for this test."""
        attempts.append(1)
        return httpx.Response(502, json={"message": "bad gateway"})

    with pytest.raises(GitHubError) as err:
        _client(handler).create_issue_comment(1, "hi")
    assert err.value.status == 502
    assert len(attempts) == 1


def test_post_is_retried_on_429() -> None:
    """A POST is retried on 429 (rate limited), because GitHub did not process it."""
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Fake GitHub endpoint for this test."""
        attempts.append(1)
        return httpx.Response(429, headers={"retry-after": "1"}) if len(attempts) == 1 else httpx.Response(201, json={})

    _client(handler).create_issue_comment(1, "hi")
    assert len(attempts) == 2


def test_missing_ref_returns_none_and_sends_auth() -> None:
    """A missing branch returns None, and requests carry the Bearer token."""
    def handler(request: httpx.Request) -> httpx.Response:
        """Fake GitHub endpoint for this test."""
        assert request.headers["authorization"] == "Bearer tok"
        assert request.url.path == "/repos/acme/sandbox/git/ref/heads/autoforge/issue-1"
        return httpx.Response(404, json={"message": "Not Found"})

    assert _client(handler).get_ref_sha("autoforge/issue-1") is None


def test_client_has_no_merge_capability() -> None:
    """The client exposes no merge method at all (PR-only guarantee)."""
    assert not any("merge" in name for name in dir(GitHubClient))


# ------------------------------------------------------------------ reading many files

def _many_files(n: int) -> dict[str, str]:
    """A repo with ``n`` small Python files."""
    return {f"src/m{i}.py": f"X = {i}\n" for i in range(n)}


def test_many_files_are_read_from_one_tarball_in_memory() -> None:
    """Above the blob limit, one tarball download replaces per-file reads; links in the archive are ignored."""
    from autoforge.integrations.github.repo import BLOB_READ_LIMIT, GitHubRepoHost
    from tests.fake_github import FakeGitHub

    fake = FakeGitHub(_many_files(BLOB_READ_LIMIT + 5))
    repo = GitHubRepoHost(fake.client(), branch_prefix="autoforge/")
    base = repo.get_base()
    files = repo.list_files(base)

    contents = repo.read_files(base, files)

    assert contents == {p: c.encode() for p, c in fake.files.items()}
    assert "evil-link" not in contents
    assert not any(path.startswith("/git/blobs") for _, path in fake.calls)
    assert ("GET", "codeload") in fake.calls
    assert fake.codeload_auth == [None]  # the token is not forwarded to the redirect host


def test_few_files_are_read_as_blobs() -> None:
    """Small reads use the blob API (no archive download)."""
    from autoforge.integrations.github.repo import GitHubRepoHost
    from tests.fake_github import FakeGitHub

    fake = FakeGitHub(_many_files(3))
    repo = GitHubRepoHost(fake.client(), branch_prefix="autoforge/")
    base = repo.get_base()

    assert len(repo.read_files(base, repo.list_files(base))) == 3
    assert ("GET", "codeload") not in fake.calls


def test_tarball_failure_falls_back_to_blobs() -> None:
    """If the archive cannot be downloaded, the first files are read one by one instead of failing the run."""
    from autoforge.integrations.github.repo import BLOB_READ_LIMIT, GitHubRepoHost
    from tests.fake_github import FakeGitHub

    fake = FakeGitHub(_many_files(BLOB_READ_LIMIT + 5))
    fake.fail[("GET", r"/tarball/.+")] = 403
    repo = GitHubRepoHost(fake.client(), branch_prefix="autoforge/")
    base = repo.get_base()

    assert len(repo.read_files(base, repo.list_files(base))) == BLOB_READ_LIMIT


def test_tarball_size_limit() -> None:
    """A download larger than the limit is aborted with a GitHubError."""
    def handler(request: httpx.Request) -> httpx.Response:
        """Serve a 2 KB archive."""
        return httpx.Response(200, content=b"x" * 2048)

    with pytest.raises(GitHubError, match="exceeds 1024 bytes"):
        _client(handler).download_tarball("abc", max_bytes=1024)
