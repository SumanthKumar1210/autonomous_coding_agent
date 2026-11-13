"""Cross-cutting observability: JSON-lines logging with a per-run correlation ID.

Every log line is one JSON object on stderr, e.g.::

    {"ts": "...", "level": "INFO", "logger": "autoforge.workflow.pipeline",
     "msg": "pull request opened", "run_id": "dff19b2d4aee", "pr": "https://..."}

The ``run_id`` is added automatically to every line logged during a run, so
you can trace one ticket from the logs to the PR footer to the issue comment.
Any ``extra={...}`` fields passed to a logger call become JSON keys.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
"""The run ID of the run currently executing (``None`` outside a run)."""

_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message", "asctime",
    "color_message",  # uvicorn's ANSI-coloured duplicate of the message
}
"""Built-in LogRecord attributes (and known noise); anything else on a record came from ``extra=``."""


@contextmanager
def run_context(run_id: str) -> Iterator[None]:
    """Tag every log record emitted inside the ``with`` block with ``run_id``.

    Example::

        with run_context("abc123"):
            log.info("step done")   # -> {"msg": "step done", "run_id": "abc123", ...}

    Uses a ``ContextVar``, so it is safe with threads and async code.
    """
    token = run_id_var.set(run_id)
    try:
        yield
    finally:
        run_id_var.reset(token)


class JsonFormatter(logging.Formatter):
    """Logging formatter that renders each record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        """Convert a log record to JSON.

        Includes timestamp (UTC, ISO 8601), level, logger name, message, the
        current ``run_id`` (if any), every ``extra=`` field, and the formatted
        traceback when the record carries exception info.
        """
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if (run_id := run_id_var.get()) is not None:
            payload["run_id"] = run_id
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger.

    Args:
        level: Minimum level to emit (``"DEBUG"``, ``"INFO"``, ``"WARNING"`` ...).

    Third-party HTTP/SDK loggers (httpx, httpcore, openai) are raised to WARNING
    so request URLs and headers never end up in the logs.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Keep third-party HTTP noise (and request URLs) out of the logs.
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
