"""Opt-in integration test against the real sandbox repository (read-only).

Skipped unless ``AUTOFORGE_INTEGRATION=1``. Uses the configured ``GITHUB_TOKEN``
and ``GITHUB_REPO`` (environment or ``.env``) and only *reads*: it creates no
branch, commit, comment, label or PR, so it is safe against the real repo.

    AUTOFORGE_INTEGRATION=1 pytest -m integration
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("AUTOFORGE_INTEGRATION") != "1", reason="set AUTOFORGE_INTEGRATION=1"),
]


def test_sandbox_repo_is_readable() -> None:
    """The token can read the default branch, its file tree and a file, through the real adapters."""
    from autoforge.app.config import Settings
    from autoforge.integrations.github.client import GitHubClient
    from autoforge.integrations.github.repo import GitHubRepoHost

    settings = Settings()
    with GitHubClient(settings.github_token.get_secret_value(), settings.github_repo,
                      base_url=settings.github_api_url) as gh:
        repo = GitHubRepoHost(gh, branch_prefix=settings.branch_prefix)
        base = repo.get_base()
        files = repo.list_files(base)

        assert base.commit_sha and files
        assert isinstance(repo.read_file(files[0]), bytes)
