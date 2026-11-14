"""Logging must keep third-party HTTP request lines (URLs, headers) out of the logs."""

from __future__ import annotations

import logging

import pytest

from autoforge.observability import configure_logging


@pytest.mark.parametrize("name", ["httpx", "httpx2", "httpcore", "openai"])
def test_http_loggers_are_quiet(name: str) -> None:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        configure_logging("DEBUG")
        assert not logging.getLogger(name).isEnabledFor(logging.INFO)
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
