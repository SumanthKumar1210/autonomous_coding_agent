"""Thin, typed wrapper over the GitHub REST API for a single repository.

Deliberately exposes no merge / approve operations: AutoForge is PR-only.
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
_IDEMPOTENT_METHODS = {"GET", "HEAD", "PUT", "DELETE", "PATCH"}


class GitHubError(Exception):
    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status


class GitHubClient:
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
        self._http.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ core

    def _request(self, method: str, path: str, *, allow_404: bool = False, **kwargs: Any) -> Any:
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

        assert resp is not None
        if allow_404 and resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise GitHubError(resp.status_code, f"{method} {path} failed: {resp.status_code} {_error_message(resp)}")
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        if retry_after and retry_after.isdigit():
            delay = min(float(retry_after), 60.0)
        else:
            delay = min(2.0 ** (attempt - 1), 30.0) + random.uniform(0, 0.5)
        self._sleep(delay)

    # ---------------------------------------------------------------- repo

    def default_branch(self) -> str:
        return str(self._request("GET", "")["default_branch"])

    def get_ref_sha(self, branch: str) -> str | None:
        data = self._request("GET", f"/git/ref/heads/{quote(branch, safe='/')}", allow_404=True)
        if data is None or isinstance(data, list):  # list = prefix match only, not this exact ref
            return None
        return str(data["object"]["sha"])

    def get_commit_tree_sha(self, commit_sha: str) -> str:
        return str(self._request("GET", f"/git/commits/{commit_sha}")["tree"]["sha"])

    def get_tree(self, tree_sha: str) -> list[dict[str, Any]]:
        data = self._request("GET", f"/git/trees/{tree_sha}", params={"recursive": "1"})
        if data.get("truncated"):
            log.warning("repository tree truncated by GitHub API", extra={"tree_sha": tree_sha})
        return list(data["tree"])

    def get_blob(self, blob_sha: str) -> bytes:
        data = self._request("GET", f"/git/blobs/{blob_sha}")
        return base64.b64decode(data["content"])

    def create_tree(self, base_tree_sha: str, entries: list[dict[str, Any]]) -> str:
        data = self._request("POST", "/git/trees", json={"base_tree": base_tree_sha, "tree": entries})
        return str(data["sha"])

    def create_commit(self, message: str, tree_sha: str, parent_sha: str) -> str:
        data = self._request("POST", "/git/commits", json={"message": message, "tree": tree_sha, "parents": [parent_sha]})
        return str(data["sha"])

    def create_branch(self, branch: str, sha: str) -> None:
        self._request("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})

    def force_update_branch(self, branch: str, sha: str) -> None:
        self._request("PATCH", f"/git/refs/heads/{quote(branch, safe='/')}", json={"sha": sha, "force": True})

    # --------------------------------------------------------------- pulls

    def find_open_pull(self, head_branch: str) -> dict[str, Any] | None:
        pulls = self._request("GET", "/pulls", params={"head": f"{self.owner}:{head_branch}", "state": "open"})
        return pulls[0] if pulls else None

    def create_pull(self, *, title: str, body: str, head: str, base: str) -> dict[str, Any]:
        return dict(self._request("POST", "/pulls", json={"title": title, "body": body, "head": head, "base": base}))

    # -------------------------------------------------------------- issues

    def get_issue(self, number: int) -> dict[str, Any]:
        return dict(self._request("GET", f"/issues/{number}"))

    def list_issue_comments(self, number: int) -> list[dict[str, Any]]:
        return list(self._request("GET", f"/issues/{number}/comments", params={"per_page": 100}))

    def create_issue_comment(self, number: int, body: str) -> None:
        self._request("POST", f"/issues/{number}/comments", json={"body": body})

    def add_issue_labels(self, number: int, labels: list[str]) -> None:
        self._request("POST", f"/issues/{number}/labels", json={"labels": labels})

    def remove_issue_label(self, number: int, label: str) -> None:
        self._request("DELETE", f"/issues/{number}/labels/{quote(label, safe='')}", allow_404=True)


def _error_message(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("message", ""))[:300]
    except ValueError:
        return resp.text[:300]
