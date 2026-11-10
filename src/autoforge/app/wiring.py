"""Composition root: the only place that knows which concrete integrations are used.

Shared by every entry point (CLI now, webhook service later).
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
    return WorkflowConfig(
        branch_prefix=settings.branch_prefix,
        max_context_bytes=settings.max_context_bytes,
        max_file_bytes=settings.max_file_bytes,
    )


@contextmanager
def build_dependencies(settings: Settings) -> Iterator[Dependencies]:
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
