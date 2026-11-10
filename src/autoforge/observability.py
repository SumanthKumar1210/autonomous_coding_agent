"""Cross-cutting observability: JSON-lines logging with a per-run correlation ID."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)

_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


@contextmanager
def run_context(run_id: str) -> Iterator[None]:
    """Tag every log record emitted inside the block with `run_id`."""
    token = run_id_var.set(run_id)
    try:
        yield
    finally:
        run_id_var.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
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
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Keep third-party HTTP noise (and request URLs) out of the logs.
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
