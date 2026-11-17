"""A ``RepoHost`` backed by a fixture folder, held in memory, for evaluation runs.

The agent graph runs unchanged against it: context is read from the fixture,
the verification sandbox gets the fixture as a tarball, and "publishing" only
records the change. Nothing is written to disk and nothing reaches GitHub.
This host exists for the eval harness only and is never wired into the
service.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

from autoforge.domain.models import BaseRef, CodeChange, PullRequest, RepoFile, RepoSnapshot
from autoforge.domain.policy import assert_writable_branch
from autoforge.evals.suite import read_tree

BRANCH_PREFIX = "autoforge/"
"""Agent branches the eval graph may "publish" to (same rule as production)."""


class LocalRepoHost:
    """Read-only, in-memory copy of a fixture repository.

    Attributes:
        files: Path -> content of every file in the fixture.
        published: Changes the graph "published", in order (branch, change).
    """

    def __init__(self, root: Path, *, name: str | None = None) -> None:
        """Load every file under ``root`` (UTF-8 text only)."""
        self.files = read_tree(root)
        self._name = name or f"eval/{root.name}"
        digest = hashlib.sha1(usedforsecurity=False)
        for path, content in self.files.items():
            digest.update(path.encode() + b"\0" + content.encode() + b"\0")
        self._base = BaseRef(branch="main", commit_sha=digest.hexdigest(), tree_sha=f"tree-{digest.hexdigest()}")
        self.published: list[tuple[str, CodeChange]] = []

    @property
    def name(self) -> str:
        """Display name, e.g. ``"eval/bms_c"``."""
        return self._name

    def get_base(self) -> BaseRef:
        """The fixture's fixed base: the "commit" SHA is a hash of its content."""
        return self._base

    def list_files(self, base: BaseRef) -> list[RepoFile]:
        """Every file in the fixture (regular files, mode ``100644``)."""
        return [RepoFile(path=p, mode="100644", size=len(c.encode()),
                         blob_id=hashlib.sha1(c.encode(), usedforsecurity=False).hexdigest())
                for p, c in self.files.items()]

    def read_files(self, base: BaseRef, files: list[RepoFile]) -> dict[str, bytes]:
        """Content of the requested files."""
        return {f.path: self.files[f.path].encode() for f in files if f.path in self.files}

    def download_archive(self, base: BaseRef) -> bytes:
        """The fixture as a gzipped tarball laid out like GitHub's (one top-level folder)."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for path, content in self.files.items():
                data = content.encode()
                info = tarfile.TarInfo(f"fixture-{base.commit_sha[:12]}/{path}")
                info.size = len(data)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def find_open_pull(self, branch: str) -> PullRequest | None:
        """Never an open pull request (every eval run starts fresh)."""
        return None

    def publish(self, snapshot: RepoSnapshot, branch: str, change: CodeChange) -> str:
        """Record the change (same branch rule as production) and return a fake commit SHA."""
        assert_writable_branch(branch, snapshot.base, required_prefix=BRANCH_PREFIX)
        self.published.append((branch, change))
        return f"eval-commit-{len(self.published)}"

    def open_pull(self, *, title: str, body: str, head: str, base: str) -> PullRequest:
        """Return a placeholder pull request (nothing is opened)."""
        return PullRequest(number=0, url=f"eval://{self._name}/{head}")
