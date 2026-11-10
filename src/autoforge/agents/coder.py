"""Coder agent backed by OpenAI structured outputs (Responses API)."""

from __future__ import annotations

import logging

from openai import OpenAI

from autoforge.agents.prompts import CODER_SYSTEM_PROMPT, build_coder_input
from autoforge.domain.errors import AgentError
from autoforge.domain.models import CodeChange, CoderResult, RepoSnapshot, Ticket, TokenUsage

log = logging.getLogger(__name__)


class OpenAICoder:
    """Implements `ports.Coder`."""

    def __init__(self, client: OpenAI, model: str, *, max_output_tokens: int = 32_000) -> None:
        self._client = client
        self._model = model
        self._max_output_tokens = max_output_tokens

    def generate(self, ticket: Ticket, snapshot: RepoSnapshot) -> CoderResult:
        response = self._client.responses.parse(
            model=self._model,
            instructions=CODER_SYSTEM_PROMPT,
            input=build_coder_input(ticket, snapshot),
            text_format=CodeChange,
            max_output_tokens=self._max_output_tokens,
        )
        usage = TokenUsage(
            input_tokens=response.usage.input_tokens if response.usage else 0,
            output_tokens=response.usage.output_tokens if response.usage else 0,
        )
        log.info("coder response", extra={"model": self._model, "status": response.status,
                                           "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens})

        if response.status == "incomplete":
            reason = response.incomplete_details.reason if response.incomplete_details else "unknown"
            raise AgentError(f"LLM response incomplete ({reason})")
        parsed = response.output_parsed
        if parsed is None:
            raise AgentError(f"LLM returned no structured change (refusal or empty output): {_refusal_text(response)}")
        return CoderResult(change=parsed, usage=usage, model=self._model)


def _refusal_text(response: object) -> str:
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            if getattr(content, "type", None) == "refusal":
                return str(getattr(content, "refusal", ""))[:300]
    return "no refusal text"
