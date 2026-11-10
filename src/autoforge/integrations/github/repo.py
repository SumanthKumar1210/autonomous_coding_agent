"""GitHub repository as a `ports.RepoHost` (Git Data API: trees, commits, refs; Pulls API)."""

from __future__ import annotations

from autoforge.domain.models import BaseRef, CodeChange, PullRequest, RepoFile, RepoSnapshot
from autoforge.domain.policy import assert_writable_branch
from autoforge.integrations.github.client import GitHubClient, GitHubError


class GitHubRepoHost:
    def __init__(self, github: GitHubClient, *, branch_prefix: str) -> None:
        self._gh = github
        self._branch_prefix = branch_prefix

    @property
    def name(self) -> str:
        return self._gh.repo

    def get_base(self) -> BaseRef:
        branch = self._gh.default_branch()
        try:
            commit_sha = self._gh.get_ref_sha(branch)
        except GitHubError as exc:
            if exc.status == 409:  # GitHub: "Git Repository is empty"
                commit_sha = None
            else:
                raise
        if commit_sha is None:
            raise RuntimeError(f"Default branch {branch!r} has no commits; push an initial commit first")
        return BaseRef(branch=branch, commit_sha=commit_sha, tree_sha=self._gh.get_commit_tree_sha(commit_sha))

    def list_files(self, base: BaseRef) -> list[RepoFile]:
        return [
            RepoFile(path=e["path"], mode=e["mode"], size=int(e.get("size", 0)), blob_id=e["sha"])
            for e in self._gh.get_tree(base.tree_sha)
            if e.get("type") == "blob"
        ]

    def read_file(self, file: RepoFile) -> bytes:
        return self._gh.get_blob(file.blob_id)

    def find_open_pull(self, branch: str) -> PullRequest | None:
        pr = self._gh.find_open_pull(branch)
        return PullRequest(number=pr["number"], url=pr["html_url"]) if pr else None

    def publish(self, snapshot: RepoSnapshot, branch: str, change: CodeChange) -> str:
        """Commit `change` on top of the base commit and point `branch` at it. Returns the commit SHA."""
        assert_writable_branch(branch, snapshot.base, required_prefix=self._branch_prefix)

        entries: list[dict[str, object]] = []
        for f in change.files:
            mode = snapshot.modes.get(f.path, "100644")
            if f.action == "delete":
                entries.append({"path": f.path, "mode": mode, "type": "blob", "sha": None})
            else:
                content = f.content if f.content.endswith("\n") else f.content + "\n"
                entries.append({"path": f.path, "mode": mode, "type": "blob", "content": content})

        tree_sha = self._gh.create_tree(snapshot.base.tree_sha, entries)
        commit_sha = self._gh.create_commit(change.commit_message, tree_sha, snapshot.base.commit_sha)
        if self._gh.get_ref_sha(branch) is None:
            self._gh.create_branch(branch, commit_sha)
        else:
            # Agent-owned branch with no open PR (stale from a failed/closed run): reset it.
            self._gh.force_update_branch(branch, commit_sha)
        return commit_sha

    def open_pull(self, *, title: str, body: str, head: str, base: str) -> PullRequest:
        pr = self._gh.create_pull(title=title, body=body, head=head, base=base)
        return PullRequest(number=pr["number"], url=pr["html_url"])
