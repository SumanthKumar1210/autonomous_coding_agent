"""Wire format between the AutoForge service and the sandbox.

Request: ``POST /run`` with an ``application/octet-stream`` body::

    4-byte big-endian length N | N bytes manifest (JSON, UTF-8) | repository archive (.tar.gz)

The archive is the base commit as GitHub serves it (one top-level folder). The
manifest says what to change and what to run::

    {"checks": [{"name": "pytest", "command": "python -m pytest -q", "timeout_seconds": 600}],
     "files": {"src/a.py": "new content", "old.py": null}}      # null = delete

Response ``200``: ``{"checks": [{"name", "command", "status", "exit_code", "output",
"duration_seconds", "preexisting"}]}`` with ``status`` one of ``passed``,
``failed``, ``timeout``, ``error``. Any other status code carries
``{"error": "..."}``.
"""

from __future__ import annotations

import json
import struct
from typing import Any

MAX_REQUEST_BYTES = 160 * 1024 * 1024
"""Largest request body the sandbox accepts (archive + manifest)."""

MAX_MANIFEST_BYTES = 32 * 1024 * 1024
"""Largest manifest (the changed files' content)."""

MAX_CHECKS = 10
"""Most checks per job."""

MAX_CHECK_SECONDS = 1800
"""Longest per-check timeout the sandbox accepts."""

STATUSES = frozenset({"passed", "failed", "timeout", "error"})
"""Valid check statuses in a response."""

_HEADER = struct.Struct(">I")


def encode_request(manifest: dict[str, Any], archive: bytes) -> bytes:
    """Build a request body from the manifest and the repository archive."""
    data = json.dumps(manifest).encode("utf-8")
    if len(data) > MAX_MANIFEST_BYTES:
        raise ValueError(f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
    return _HEADER.pack(len(data)) + data + archive


def decode_request(body: bytes) -> tuple[dict[str, Any], bytes]:
    """Split and validate a request body.

    Returns:
        ``(manifest, archive)``.

    Raises:
        ValueError: If the body is malformed or the manifest is invalid.
    """
    if len(body) < _HEADER.size:
        raise ValueError("request too short")
    (size,) = _HEADER.unpack_from(body)
    if size > MAX_MANIFEST_BYTES or _HEADER.size + size > len(body):
        raise ValueError("invalid manifest length")
    manifest = json.loads(body[_HEADER.size:_HEADER.size + size].decode("utf-8"))
    validate_manifest(manifest)
    return manifest, body[_HEADER.size + size:]


def validate_manifest(manifest: Any) -> None:
    """Check the manifest's structure and limits.

    Raises:
        ValueError: On the first problem found.
    """
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    checks, files = manifest.get("checks"), manifest.get("files")
    if not isinstance(checks, list) or not 0 < len(checks) <= MAX_CHECKS:
        raise ValueError(f"'checks' must list 1-{MAX_CHECKS} checks")
    for c in checks:
        if not (isinstance(c, dict) and isinstance(c.get("name"), str) and isinstance(c.get("command"), str)
                and c["command"].strip()):
            raise ValueError("each check needs a 'name' and a non-empty 'command'")
        timeout = c.get("timeout_seconds")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 0 < timeout <= MAX_CHECK_SECONDS:
            raise ValueError(f"'timeout_seconds' must be 1-{MAX_CHECK_SECONDS}")
    if not isinstance(files, dict) or not all(
            isinstance(k, str) and (v is None or isinstance(v, str)) for k, v in files.items()):
        raise ValueError("'files' must map paths to content or null")


def job_seconds(manifest: dict[str, Any]) -> int:
    """Upper bound for a whole job: every check twice (change + base) plus unpacking."""
    return 2 * sum(int(c["timeout_seconds"]) for c in manifest["checks"]) + 120
