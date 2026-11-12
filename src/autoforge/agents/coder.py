"""Coder agent backed by OpenAI structured outputs (Responses API).

"Structured outputs" means OpenAI is given the JSON schema of ``CodeChange``
and must answer with JSON matching it exactly, so there is no fragile text
parsing. The call itself lives in ``agents.llm``.

On a retry the coder also receives a ``Revision``: its previous change and
the feedback (reviewer findings or a policy error) it must address.
"""

from __future__ import annotations

from openai import OpenAI

from autoforge.agents.llm import structured_call
from autoforge.agents.prompts import CODER_SYSTEM_PROMPT, build_coder_input
from autoforge.domain.models import CodeChange, CoderResult, RepoSnapshot, Revision, Ticket


class OpenAICoder:
    """Coder agent that asks an OpenAI model for a ``CodeChange``.

    Implements the ``ports.Coder`` interface.

    Example::

        coder = OpenAICoder(OpenAI(api_key=...), "gpt-6-luna")
        result = coder.generate(ticket, snapshot)
        result.change.files   # -> list of FileChange
    """

    def __init__(self, client: OpenAI, model: str, *, max_output_tokens: int = 32_000) -> None:
        """Create the agent.

        Args:
            client: Configured OpenAI SDK client (API key, timeout).
            model: Model ID, e.g. ``"gpt-6-luna"`` (from ``OPENAI_MODEL``).
            max_output_tokens: Upper bound on generated tokens; if the model hits
                it, the run fails with ``AgentError`` rather than using a
                half-written change.
        """
        self._client = client
        self._model = model
        self._max_output_tokens = max_output_tokens

    def generate(self, ticket: Ticket, snapshot: RepoSnapshot, revision: Revision | None = None) -> CoderResult:
        """Ask the model to implement ``ticket`` and return its structured change.

        Args:
            ticket: The ticket to implement.
            snapshot: Repository context (tree + selected file contents).
            revision: Previous attempt and feedback to address, or ``None``.

        Returns:
            The parsed ``CodeChange`` plus token usage and model ID.

        Raises:
            AgentError: If the response was truncated or the model refused.
        """
        change, usage = structured_call(
            self._client,
            model=self._model,
            instructions=CODER_SYSTEM_PROMPT,
            input_text=build_coder_input(ticket, snapshot, revision),
            schema=CodeChange,
            max_output_tokens=self._max_output_tokens,
            agent="coder",
        )
        return CoderResult(change=change, usage=usage, model=self._model)
