"""Domain-level exceptions."""


class AutoForgeError(Exception):
    """Base class for expected, reportable failures."""


class TicketError(AutoForgeError):
    """The ticket cannot be worked on (not found, closed, wrong type, bad key)."""


class ChangeValidationError(AutoForgeError):
    """The agent's proposed change violates change policy."""


class SafetyError(AutoForgeError):
    """An operation would violate a hard constraint (e.g. writing to the default branch)."""


class AgentError(AutoForgeError):
    """An agent failed to produce a usable result (refusal, truncation, empty output)."""
