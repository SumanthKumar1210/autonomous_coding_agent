"""Thin, typed wrapper over the GitHub REST API for a single repository.

Deliberately exposes **no merge / approve operations**: AutoForge is PR-only.
A test (``test_client_has_no_merge_capability``) guards this.

Retry policy (why it is careful):

- Network errors before the request was sent -> safe to retry any method.
- 429 (rate limited) -> GitHub did not process the request -> retry any method.
- 5xx / mid-request network errors -> retry only idempotent methods (GET, PUT,
  PATCH, DELETE). A POST (create PR, create comment) is *not* retried, because
  GitHub may have processed it already and a retry could create a duplicate.
"""

from __future__ import annotations

import base64
import logging
import random
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
"""HTTP statuses that indicate a temporary problem worth retrying."""

_IDEMPOTENT_METHODS = {"GET", "HEAD", "PUT", "DELETE", "PATCH"}
"""Methods that are safe to repeat: doing them twice has the same effect as once."""


class GitHubError(Exception):
    """A GitHub API call failed.

    Attributes:
        status: HTTP status code (e.g. 404, 409), or ``None`` for network errors.
    """

    def __init__(self, status: int | None, message: str) -> None:
        """Create the error.

        Args:
            status: HTTP status code, or ``None`` if no response was received.
            message: Human-readable description, including GitHub's error message.
        """
        super().__init__(message)
        self.status = status


class GitHubClient:
    """REST client bound to one repository (``owner/repo``).

    Every method maps to one GitHub endpoint and returns plain JSON data
    (dicts/lists). Higher-level meaning (tickets, snapshots, PR objects) is
    added by ``issues.py`` and ``repo.py``.

    Usable as a context manager so the HTTP connection pool is closed::

        with GitHubClient(token, "owner/repo") as gh:
            gh.default_branch()
    """

    def __init__(
        self,
        token: str,
        repo: str,
        *,
        base_url: str = "https://api.github.com",
        timeout: float = 30.0,
        max_attempts: int = 3,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create the client.

        Args:
            token: Fine-grained personal access token (or GitHub App token).
            repo: Repository as ``"owner/repo"``.
            base_url: API root; override for GitHub Enterprise.
            timeout: Per-request timeout in seconds.
            max_attempts: Total tries per request, including the first.
            transport: Custom httpx transport. Tests pass ``httpx.MockTransport``
                to talk to an in-memory fake instead of the network.
            sleep: Function used to wait between retries (tests pass a no-op).
        """
        self.repo = repo
        self.owner = repo.split("/", 1)[0]
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "autoforge",
            },
        )

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._http.close()

    def __enter__(self) -> GitHubClient:
        """Enter a ``with`` block; returns the client itself."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Leave a ``with`` block; closes the client."""
        self.close()

    # ------------------------------------------------------------------ core

    def _request(self, method: str, path: str, *, allow_404: bool = False, **kwargs: Any) -> Any:
        """Send one API request with retries and error handling.

        Args:
            method: HTTP method (``"GET"``, ``"POST"``, ...).
            path: Path relative to ``/repos/{owner}/{repo}``, e.g. ``"/pulls"``.
                An empty string addresses the repository itself.
            allow_404: Return ``None`` instead of raising when GitHub answers 404.
            **kwargs: Passed to ``httpx.Client.request`` (``json=``, ``params=`` ...).

        Returns:
            Parsed JSON response, or ``None`` for empty responses (and allowed 404s).

        Raises:
            GitHubError: On any non-2xx response (after retries) or network failure.
        """
        url = f"/repos/{self.repo}{path}"
        resp: httpx.Response | None = None
        for attempt in range(1, self._max_attempts + 1):
            last = attempt == self._max_attempts
            try:
                resp = self._http.request(method, url, **kwargs)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # Request never reached the server: safe to retry for any method.
                if last:
                    raise GitHubError(None, f"{method} {path}: {exc}") from exc
                self._backoff(attempt, None)
                continue
            except httpx.TransportError as exc:
                # Failed mid-request: the server may have acted on it, so only
                # idempotent methods are retried.
                if last or method not in _IDEMPOTENT_METHODS:
                    raise GitHubError(None, f"{method} {path}: {exc}") from exc
                self._backoff(attempt, None)
                continue

            retryable = resp.status_code == 429 or (
                resp.status_code in _RETRYABLE_STATUSES and method in _IDEMPOTENT_METHODS
            )
            if retryable and not last:
                log.warning("github retry", extra={"method": method, "path": path, "status": resp.status_code})
                self._backoff(attempt, resp.headers.get("retry-after"))
                continue
            break

        if resp is None:  # unreachable: the loop runs at least once and returns or raises
            raise GitHubError(None, f"{method} {path}: no response")
        if allow_404 and resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise GitHubError(resp.status_code, f"{method} {path} failed: {resp.status_code} {_error_message(resp)}")
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        """Wait before the next retry.

        Honours GitHub's ``Retry-After`` header (capped at 60 s); otherwise uses
        exponential backoff (1 s, 2 s, 4 s ... capped at 30 s) plus a little
        random jitter so parallel clients do not retry in lockstep.
        """
        if retry_after and retry_after.isdigit():
            delay = min(float(retry_after), 60.0)
        else:
            delay = min(2.0 ** (attempt - 1), 30.0) + random.uniform(0, 0.5)  # noqa: S311 (retry jitter, not crypto)
        self._sleep(delay)

    # ---------------------------------------------------------------- repo

    def default_branch(self) -> str:
        """Return the repository's default branch name (e.g. ``"main"``)."""
        return str(self._request("GET", "")["default_branch"])

    def get_ref_sha(self, branch: str) -> str | None:
        """Return the commit SHA a branch points to, or ``None`` if it does not exist.

        Raises:
            GitHubError: e.g. 409 when the repository is completely empty.
        """
        data = self._request("GET", f"/git/ref/heads/{quote(branch, safe='/')}", allow_404=True)
        if data is None or isinstance(data, list):  # list = prefix match only, not this exact ref
            return None
        return str(data["object"]["sha"])

    def get_commit_tree_sha(self, commit_sha: str) -> str:
        """Return the SHA of the file tree belonging to a commit."""
        return str(self._request("GET", f"/git/commits/{commit_sha}")["tree"]["sha"])

    def get_tree(self, tree_sha: str) -> list[dict[str, Any]]:
        """List every entry (files and folders) in a tree, recursively.

        Each entry is a dict with ``path``, ``mode``, ``type`` (``"blob"`` = file,
        ``"tree"`` = folder), ``sha`` and (for files) ``size``. Logs a warning
        if GitHub truncated the listing (very large repositories).
        """
        data = self._request("GET", f"/git/trees/{tree_sha}", params={"recursive": "1"})
        if data.get("truncated"):
            log.warning("repository tree truncated by GitHub API", extra={"tree_sha": tree_sha})
        return list(data["tree"])

    def get_blob(self, blob_sha: str) -> bytes:
        """Download a file's raw content by its blob SHA (GitHub returns base64)."""
        data = self._request("GET", f"/git/blobs/{blob_sha}")
        return base64.b64decode(data["content"])

    def create_tree(self, base_tree_sha: str, entries: list[dict[str, Any]]) -> str:
        """Create a new tree = the base tree with ``entries`` added/changed/removed.

        Args:
            base_tree_sha: Tree to start from (the base commit's tree).
            entries: Git tree entries. ``{"path", "mode", "type": "blob", "content"}``
                writes a file; ``"sha": None`` instead of content deletes it.

        Returns:
            SHA of the new tree.
        """
        data = self._request("POST", "/git/trees", json={"base_tree": base_tree_sha, "tree": entries})
        return str(data["sha"])

    def create_commit(self, message: str, tree_sha: str, parent_sha: str) -> str:
        """Create a commit object (not yet on any branch).

        Args:
            message: Commit message.
            tree_sha: Tree that the commit snapshots.
            parent_sha: Parent commit (the base commit).

        Returns:
            SHA of the new commit.
        """
        data = self._request("POST", "/git/commits",
                             json={"message": message, "tree": tree_sha, "parents": [parent_sha]})
        return str(data["sha"])

    def create_branch(self, branch: str, sha: str) -> None:
        """Create a new branch pointing at commit ``sha``."""
        self._request("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})

    def force_update_branch(self, branch: str, sha: str) -> None:
        """Move an existing branch to commit ``sha``, discarding its old commits.

        Only ever called for agent-owned ``autoforge/*`` branches (enforced by
        ``domain.policy.assert_writable_branch`` before publishing).
        """
        self._request("PATCH", f"/git/refs/heads/{quote(branch, safe='/')}", json={"sha": sha, "force": True})

    # --------------------------------------------------------------- pulls

    def find_open_pull(self, head_branch: str) -> dict[str, Any] | None:
        """Return the first open PR whose source branch is ``head_branch``, or ``None``."""
        pulls = self._request("GET", "/pulls", params={"head": f"{self.owner}:{head_branch}", "state": "open"})
        return pulls[0] if pulls else None

    def create_pull(self, *, title: str, body: str, head: str, base: str) -> dict[str, Any]:
        """Open a pull request from branch ``head`` into branch ``base``.

        Returns:
            GitHub's PR object (includes ``number`` and ``html_url``).
        """
        return dict(self._request("POST", "/pulls", json={"title": title, "body": body, "head": head, "base": base}))

    # -------------------------------------------------------------- issues

    def get_issue(self, number: int) -> dict[str, Any]:
        """Fetch an issue. Note: GitHub also returns PRs here (they have a ``pull_request`` key)."""
        return dict(self._request("GET", f"/issues/{number}"))

    def list_issue_comments(self, number: int) -> list[dict[str, Any]]:
        """List an issue's comments, oldest first (first 100 only)."""
        return list(self._request("GET", f"/issues/{number}/comments", params={"per_page": 100}))

    def create_issue_comment(self, number: int, body: str) -> None:
        """Post a Markdown comment on an issue."""
        self._request("POST", f"/issues/{number}/comments", json={"body": body})

    def add_issue_labels(self, number: int, labels: list[str]) -> None:
        """Add labels to an issue (GitHub creates labels that do not exist yet)."""
        self._request("POST", f"/issues/{number}/labels", json={"labels": labels})

    def get_collaborator_permission(self, login: str) -> str:
        """Return ``login``'s permission on the repo: ``"admin"``, ``"write"``, ``"read"`` or ``"none"``.

        GitHub reports ``maintain`` as ``"write"`` and ``triage`` as ``"read"``
        here. Requires the token's Metadata (read) permission.
        """
        data = self._request("GET", f"/collaborators/{quote(login, safe='')}/permission", allow_404=True)
        return str(data.get("permission", "none")) if data else "none"

    def remove_issue_label(self, number: int, label: str) -> None:
        """Remove a label from an issue; does nothing if the label is not there."""
        self._request("DELETE", f"/issues/{number}/labels/{quote(label, safe='')}", allow_404=True)


def _error_message(resp: httpx.Response) -> str:
    """Extract GitHub's error message from a failed response (max 300 chars)."""
    try:
        return str(resp.json().get("message", ""))[:300]
    except ValueError:
        return resp.text[:300]
