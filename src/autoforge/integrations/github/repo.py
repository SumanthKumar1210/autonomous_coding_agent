"""GitHub repository as a ``ports.RepoHost``.

Uses GitHub's *Git Data API* (trees, commits, refs) instead of the simpler
"create/update file" endpoint. Why: a multi-file change becomes one atomic
commit, created entirely server-side, without cloning the repository.

How a change is published:

1. ``create_tree``   - base tree + the changed/new/deleted files -> new tree
2. ``create_commit`` - new tree with the base commit as parent  -> new commit
3. create or move the agent branch (``autoforge/issue-N``) to that commit
4. ``open_pull``     - PR from the agent branch into the default branch
"""

from __future__ import annotations

from autoforge.domain.models import BaseRef, CodeChange, PullRequest, RepoFile, RepoSnapshot
from autoforge.domain.policy import assert_writable_branch
from autoforge.integrations.github.client import GitHubClient, GitHubError


class GitHubRepoHost:
    """A GitHub repository that AutoForge reads code from and opens PRs on.

    Implements the ``ports.RepoHost`` interface. Has no merge capability.
    """

    def __init__(self, github: GitHubClient, *, branch_prefix: str) -> None:
        """Create the repo host.

        Args:
            github: REST client for the target repository.
            branch_prefix: Prefix of agent-owned branches (e.g. ``"autoforge/"``);
                ``publish`` refuses to write to any other branch.
        """
        self._gh = github
        self._branch_prefix = branch_prefix

    @property
    def name(self) -> str:
        """Repository name as ``"owner/repo"``."""
        return self._gh.repo

    def get_base(self) -> BaseRef:
        """Return the current head of the default branch (commit + tree SHA).

        Raises:
            RuntimeError: If the repository has no commits yet (GitHub answers 409),
                with a message telling the user to push an initial commit.
        """
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
        """List every file (not folders) in the repository at ``base``."""
        return [
            RepoFile(path=e["path"], mode=e["mode"], size=int(e.get("size", 0)), blob_id=e["sha"])
            for e in self._gh.get_tree(base.tree_sha)
            if e.get("type") == "blob"
        ]

    def read_file(self, file: RepoFile) -> bytes:
        """Download one file's raw bytes."""
        return self._gh.get_blob(file.blob_id)

    def find_open_pull(self, branch: str) -> PullRequest | None:
        """Return the open PR from ``branch``, or ``None`` (used for idempotency)."""
        pr = self._gh.find_open_pull(branch)
        return PullRequest(number=pr["number"], url=pr["html_url"]) if pr else None

    def publish(self, snapshot: RepoSnapshot, branch: str, change: CodeChange) -> str:
        """Commit ``change`` on top of the base commit and point ``branch`` at it.

        The safety check runs first, so nothing is written unless ``branch`` is
        an agent-owned branch. File modes (e.g. executable) are preserved, and
        every written file is given a trailing newline.

        If the branch already exists (left over from a failed or closed run with
        no open PR), it is reset to the new commit.

        Args:
            snapshot: The repository state the change was made against.
            branch: Agent branch to write, e.g. ``"autoforge/issue-7"``.
            change: The validated change to commit.

        Returns:
            SHA of the new commit.

        Raises:
            SafetyError: If ``branch`` is the default branch or not an agent branch.
        """
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
        """Open a pull request from ``head`` (agent branch) into ``base`` (default branch)."""
        pr = self._gh.create_pull(title=title, body=body, head=head, base=base)
        return PullRequest(number=pr["number"], url=pr["html_url"])
