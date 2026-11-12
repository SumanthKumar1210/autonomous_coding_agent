"""Composition root: the only place that knows which concrete classes are used.

Everything else depends on interfaces (``ports``). This module decides that
"tickets" means GitHub Issues, "repo" means the GitHub repository, and "coder"
means the OpenAI coder, and connects them using the settings.

To switch to Jira later, only this file changes. Every entry point (CLI now,
webhook service later) uses ``build_dependencies``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from openai import OpenAI

from autoforge.agents.coder import OpenAICoder
from autoforge.app.config import Settings
from autoforge.integrations.github.client import GitHubClient
from autoforge.integrations.github.issues import GitHubIssuesSource
from autoforge.integrations.github.repo import GitHubRepoHost
from autoforge.workflow.pipeline import Dependencies, WorkflowConfig


def workflow_config(settings: Settings) -> WorkflowConfig:
    """Extract the workflow-related settings into a ``WorkflowConfig``."""
    return WorkflowConfig(
        branch_prefix=settings.branch_prefix,
        max_context_bytes=settings.max_context_bytes,
        max_file_bytes=settings.max_file_bytes,
    )


@contextmanager
def build_dependencies(settings: Settings) -> Iterator[Dependencies]:
    """Create the real GitHub and OpenAI collaborators for a run.

    Used as a context manager so network clients are always closed::

        with build_dependencies(settings) as deps:
            run_ticket("12", deps, workflow_config(settings))

    One ``GitHubClient`` is shared by the ticket source and the repo host
    (same repository, same connection pool).

    Args:
        settings: Loaded configuration.

    Yields:
        ``Dependencies`` with GitHub Issues as tickets, the GitHub repo as repo
        host, and the OpenAI coder.
    """
    github = GitHubClient(
        settings.github_token.get_secret_value(),
        settings.github_repo,
        base_url=settings.github_api_url,
        timeout=settings.http_timeout_seconds,
    )
    openai_client = OpenAI(api_key=settings.openai_api_key.get_secret_value(),
                           timeout=settings.openai_timeout_seconds)
    try:
        yield Dependencies(
            tickets=GitHubIssuesSource(github, label_prefix=settings.trigger_label),
            repo=GitHubRepoHost(github, branch_prefix=settings.branch_prefix),
            coder=OpenAICoder(openai_client, settings.openai_model,
                              max_output_tokens=settings.openai_max_output_tokens),
        )
    finally:
        github.close()
        openai_client.close()
