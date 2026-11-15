"""The sandbox container as a ``ports.Verifier``.

The service never runs repository code itself. It sends the base commit's
archive, the changed files and the checks to the sandbox (``autoforge.sandbox``,
a separate container without internet access) and turns the answer into a
``VerificationReport``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from autoforge.domain.errors import VerificationError
from autoforge.domain.models import CheckResult, CheckSpec, CheckStatus, CodeChange, VerificationReport
from autoforge.sandbox.protocol import STATUSES, encode_request

log = logging.getLogger(__name__)

CONNECT_ATTEMPTS = 3
"""Tries to reach the sandbox (only connection failures are retried: a job is never sent twice)."""


class SandboxVerifier:
    """Runs a change's checks in the sandbox container over HTTP.

    Implements ``ports.Verifier``.
    """

    def __init__(self, url: str, *, timeout: float, transport: httpx.BaseTransport | None = None) -> None:
        """Create the client.

        Args:
            url: Sandbox base URL, e.g. ``"http://sandbox:8090"``.
            timeout: Longest wait for one job, in seconds (checks can take minutes).
            transport: Custom transport (tests only).
        """
        self._http = httpx.Client(base_url=url, timeout=httpx.Timeout(timeout, connect=10.0), transport=transport)

    def close(self) -> None:
        """Close the HTTP connection pool."""
        self._http.close()

    def verify(self, archive: bytes, change: CodeChange, checks: list[CheckSpec]) -> VerificationReport:
        """Apply ``change`` to the archived base commit and run ``checks`` in the sandbox.

        Raises:
            VerificationError: If the sandbox is unreachable, times out, or answers with an error.
        """
        if not checks:
            return VerificationReport(skipped="no checks configured or detected")
        manifest = {
            "checks": [{"name": c.name, "command": c.command, "timeout_seconds": c.timeout_seconds} for c in checks],
            "files": {f.path: None if f.action == "delete" else f.content for f in change.files},
        }
        body = encode_request(manifest, archive)
        resp = self._post(body)
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        if resp.status_code != httpx.codes.OK:
            error = payload.get("error") if isinstance(payload, dict) else None
            raise VerificationError(f"sandbox answered {resp.status_code}: {error or resp.text[:500]}")
        return VerificationReport(checks=[_result(r) for r in _checks(payload)])

    def _post(self, body: bytes) -> httpx.Response:
        """POST the job, retrying only when the connection could not be made."""
        for attempt in range(1, CONNECT_ATTEMPTS + 1):
            try:
                return self._http.post("/run", content=body, headers={"Content-Type": "application/octet-stream"})
            except httpx.ConnectError as exc:
                if attempt == CONNECT_ATTEMPTS:
                    raise VerificationError(f"sandbox unreachable: {exc}") from exc
                log.warning("sandbox unreachable; retrying", extra={"attempt": attempt})
                time.sleep(2.0 * attempt)
            except httpx.HTTPError as exc:  # timeouts and broken connections: the job may have run, do not resend
                raise VerificationError(f"sandbox request failed: {type(exc).__name__}: {exc}") from exc
        raise VerificationError("sandbox unreachable")  # unreachable: the last attempt returns or raises


def _checks(payload: Any) -> list[dict[str, Any]]:
    """Return the ``checks`` list of a response, or raise if it is malformed."""
    checks = payload.get("checks") if isinstance(payload, dict) else None
    if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
        raise VerificationError("sandbox response has no 'checks' list")
    return checks


def _result(raw: dict[str, Any]) -> CheckResult:
    """Convert one response entry into a ``CheckResult``."""
    status = raw.get("status")
    if status not in STATUSES:
        raise VerificationError(f"sandbox returned an unknown check status: {status!r}")
    exit_code = raw.get("exit_code")
    checked: CheckStatus = status
    return CheckResult(
        name=str(raw.get("name", "")), command=str(raw.get("command", "")), status=checked,
        exit_code=exit_code if isinstance(exit_code, int) else None, output=str(raw.get("output", "")),
        duration_seconds=float(raw.get("duration_seconds") or 0.0), preexisting=bool(raw.get("preexisting")),
    )
