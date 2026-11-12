"""Reviewer (critic) agent backed by OpenAI structured outputs.

The reviewer reads the ticket, the repository context and the coder's
proposed change, and returns a ``ReviewVerdict``: approve or reject, with
findings graded blocker / major / minor. The workflow loops rejected changes
back to the coder, up to a bounded number of attempts.

The reviewer only judges. It cannot edit the change or touch GitHub.
"""

from __future__ import annotations

from openai import OpenAI

from autoforge.agents.llm import structured_call
from autoforge.agents.prompts import REVIEWER_SYSTEM_PROMPT, build_reviewer_input
from autoforge.domain.models import CodeChange, RepoSnapshot, ReviewerResult, ReviewVerdict, Ticket


class OpenAIReviewer:
    """Reviewer agent that asks an OpenAI model for a ``ReviewVerdict``.

    Implements the ``ports.Reviewer`` interface.

    Example::

        reviewer = OpenAIReviewer(OpenAI(api_key=...), "gpt-6-luna")
        result = reviewer.review(ticket, snapshot, change)
        result.verdict.passed   # -> True / False
    """

    def __init__(self, client: OpenAI, model: str, *, max_output_tokens: int = 16_000) -> None:
        """Create the agent.

        Args:
            client: Configured OpenAI SDK client.
            model: Model ID (``OPENAI_REVIEWER_MODEL``, defaulting to ``OPENAI_MODEL``).
            max_output_tokens: Upper bound on generated tokens.
        """
        self._client = client
        self._model = model
        self._max_output_tokens = max_output_tokens

    def review(self, ticket: Ticket, snapshot: RepoSnapshot, change: CodeChange) -> ReviewerResult:
        """Review ``change`` against ``ticket`` and the repository.

        Returns:
            The verdict plus token usage and model ID.

        Raises:
            AgentError: If the response was truncated or the model refused.
        """
        verdict, usage = structured_call(
            self._client,
            model=self._model,
            instructions=REVIEWER_SYSTEM_PROMPT,
            input_text=build_reviewer_input(ticket, snapshot, change),
            schema=ReviewVerdict,
            max_output_tokens=self._max_output_tokens,
            agent="reviewer",
        )
        return ReviewerResult(verdict=verdict, usage=usage, model=self._model)
