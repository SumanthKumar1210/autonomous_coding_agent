"""Domain-level exception types.

Each class names a *kind* of expected failure, so the workflow can report a
clear reason on the ticket instead of a raw stack trace. All of them derive
from ``AutoForgeError``.
"""


class AutoForgeError(Exception):
    """Base class for expected, reportable AutoForge failures."""


class TicketError(AutoForgeError):
    """The ticket cannot be worked on.

    Examples: the issue does not exist, is closed, is actually a pull request,
    or the key is not a valid issue number.
    """


class ChangeValidationError(AutoForgeError):
    """The agent's proposed change breaks a change-policy rule.

    Examples: a path escapes the repo (``../``), touches ``.github/workflows/``,
    or modifies a file the agent was never shown.
    """


class SafetyError(AutoForgeError):
    """An operation would violate a hard constraint.

    Example: writing to the default branch instead of an ``autoforge/*`` branch.
    These indicate a bug and must never be "worked around".
    """


class AgentError(AutoForgeError):
    """An LLM agent failed to produce a usable result.

    Examples: the model refused, its output was cut off by the token limit, or
    it returned nothing parseable.
    """


class ReviewRejectedError(AgentError):
    """The reviewer did not approve the change within the allowed number of attempts.

    The message lists the unresolved findings so they can be posted on the
    ticket. No pull request is opened.
    """
