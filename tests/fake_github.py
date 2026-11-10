"""In-memory fake of the GitHub REST endpoints AutoForge uses, served via httpx.MockTransport."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

import httpx

from autoforge.integrations.github.client import GitHubClient

REPO = "acme/sandbox"


class FakeGitHub:
    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = files if files is not None else {"README.md": "# Sandbox\n", "src/app.py": "print('hi')\n"}
        self.blobs = {f"blob-{p}": c.encode() for p, c in self.files.items()}
        self.refs: dict[str, str] = {"main": "commit-base"}
        self.issues: dict[int, dict[str, Any]] = {}
        self.comments: dict[int, list[str]] = {}
        self.labels: dict[int, list[str]] = {}
        self.pulls: list[dict[str, Any]] = []
        self.created_trees: list[dict[str, Any]] = []
        self.created_commits: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []
        self.fail: dict[tuple[str, str], int] = {}  # (method, path-regex) -> status to return

    def add_issue(self, number: int, title: str = "Add feature", body: str = "Please add it", **extra: Any) -> None:
        self.issues[number] = {"number": number, "title": title, "body": body, "state": "open",
                               "html_url": f"https://github.com/{REPO}/issues/{number}", "labels": [], **extra}
        self.comments[number] = []
        self.labels[number] = []

    def client(self) -> GitHubClient:
        return GitHubClient("test-token", REPO, transport=httpx.MockTransport(self.handle), sleep=lambda _: None)

    # ------------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path.removeprefix(f"/repos/{REPO}")
        self.calls.append((method, path))
        for (m, pattern), status in self.fail.items():
            if m == method and re.fullmatch(pattern, path):
                return httpx.Response(status, json={"message": "injected failure"})
        body = json.loads(request.content) if request.content else {}

        def ok(data: Any, status: int = 200) -> httpx.Response:
            return httpx.Response(status, json=data)

        if method == "GET" and path == "":
            return ok({"default_branch": "main"})
        if m := re.fullmatch(r"/git/ref/heads/(.+)", path):
            if method == "GET":
                sha = self.refs.get(m[1])
                return ok({"object": {"sha": sha}}) if sha else httpx.Response(404, json={"message": "Not Found"})
        if method == "PATCH" and (m := re.fullmatch(r"/git/refs/heads/(.+)", path)):
            self.refs[m[1]] = body["sha"]
            return ok({})
        if method == "POST" and path == "/git/refs":
            self.refs[body["ref"].removeprefix("refs/heads/")] = body["sha"]
            return ok({}, 201)
        if method == "GET" and (m := re.fullmatch(r"/git/commits/(.+)", path)):
            return ok({"tree": {"sha": "tree-base"}})
        if method == "GET" and path == "/git/trees/tree-base":
            tree = [{"path": p, "mode": "100644", "type": "blob", "sha": f"blob-{p}", "size": len(c)}
                    for p, c in self.files.items()]
            return ok({"tree": tree, "truncated": False})
        if method == "GET" and (m := re.fullmatch(r"/git/blobs/(.+)", path)):
            return ok({"content": base64.b64encode(self.blobs[m[1]]).decode(), "encoding": "base64"})
        if method == "POST" and path == "/git/trees":
            self.created_trees.append(body)
            return ok({"sha": f"tree-{len(self.created_trees)}"}, 201)
        if method == "POST" and path == "/git/commits":
            self.created_commits.append(body)
            return ok({"sha": f"commit-{len(self.created_commits)}"}, 201)
        if path == "/pulls":
            if method == "GET":
                head = request.url.params["head"].split(":", 1)[1]
                return ok([p for p in self.pulls if p["head"] == head and p["state"] == "open"])
            if method == "POST":
                pr = {**body, "state": "open", "number": 100 + len(self.pulls),
                      "html_url": f"https://github.com/{REPO}/pull/{100 + len(self.pulls)}"}
                self.pulls.append(pr)
                return ok(pr, 201)
        if m := re.fullmatch(r"/issues/(\d+)", path):
            issue = self.issues.get(int(m[1]))
            return ok(issue) if issue else httpx.Response(404, json={"message": "Not Found"})
        if m := re.fullmatch(r"/issues/(\d+)/comments", path):
            n = int(m[1])
            if method == "GET":
                return ok([{"body": c} for c in self.comments[n]])
            self.comments[n].append(body["body"])
            return ok({}, 201)
        if method == "POST" and (m := re.fullmatch(r"/issues/(\d+)/labels", path)):
            self.labels[int(m[1])].extend(body["labels"])
            return ok([])
        if method == "DELETE" and (m := re.fullmatch(r"/issues/(\d+)/labels/(.+)", path)):
            labels = self.labels[int(m[1])]
            if m[2] in labels:
                labels.remove(m[2])
                return ok([])
            return httpx.Response(404, json={"message": "Label does not exist"})
        if method == "PUT" and re.fullmatch(r"/pulls/\d+/merge", path):
            raise AssertionError("AutoForge must never merge a pull request")
        return httpx.Response(501, json={"message": f"fake: unhandled {method} {path}"})
