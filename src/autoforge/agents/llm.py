"""Shared OpenAI plumbing for the agents: one structured-output call.

Both the coder and the reviewer ask OpenAI for JSON matching a pydantic model
("structured outputs" via the Responses API). This module holds that call and
its error handling once, so each agent only supplies its prompt and schema.
"""

from __future__ import annotations

import logging
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel

from autoforge.domain.errors import AgentError
from autoforge.domain.models import TokenUsage

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def structured_call(
    client: OpenAI,
    *,
    model: str,
    instructions: str,
    input_text: str,
    schema: type[T],
    max_output_tokens: int,
    agent: str,
) -> tuple[T, TokenUsage]:
    """Call the model and return its answer parsed as ``schema``.

    Args:
        client: Configured OpenAI SDK client.
        model: Model ID, e.g. ``"gpt-6-luna"``.
        instructions: System prompt (role and rules).
        input_text: User message (ticket, repository, ...).
        schema: Pydantic model the answer must match.
        max_output_tokens: Upper bound on generated tokens.
        agent: Agent name for logs, e.g. ``"coder"`` or ``"reviewer"``.

    Returns:
        The parsed answer and the token usage of the call.

    Raises:
        AgentError: If the response was truncated (``status == "incomplete"``),
            or the model refused / returned no parseable output.
    """
    response = client.responses.parse(
        model=model,
        instructions=instructions,
        input=input_text,
        text_format=schema,
        max_output_tokens=max_output_tokens,
    )
    usage = TokenUsage(
        input_tokens=response.usage.input_tokens if response.usage else 0,
        output_tokens=response.usage.output_tokens if response.usage else 0,
    )
    log.info(f"{agent} response", extra={"model": model, "status": response.status,
                                        "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens})

    if response.status == "incomplete":
        reason = response.incomplete_details.reason if response.incomplete_details else "unknown"
        raise AgentError(f"{agent} LLM response incomplete ({reason})")
    parsed = response.output_parsed
    if parsed is None:
        raise AgentError(f"{agent} LLM returned no structured output (refusal or empty): {refusal_text(response)}")
    return parsed, usage


def refusal_text(response: object) -> str:
    """Extract the model's refusal message from a response, if it gave one.

    Used only to make the error posted on the ticket more informative.
    Returns at most 300 characters, or ``"no refusal text"``.
    """
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            if getattr(content, "type", None) == "refusal":
                return str(getattr(content, "refusal", ""))[:300]
    return "no refusal text"
