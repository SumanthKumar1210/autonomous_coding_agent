"""HTTP front end of the sandbox container: one job at a time, each as the unprivileged job user.

Run inside the sandbox image (``docker compose`` service ``sandbox``)::

    python -I -m autoforge.sandbox.server --port 8090 --work-dir /work --job-user runner

Endpoints:

- ``GET /healthz`` - liveness (answers while a job runs).
- ``POST /run`` - run one job (format in ``sandbox.protocol``); jobs are
  serialised, so a second request waits for the first.

Isolation (with ``--job-user``; the container starts the server as root with
only the ``SETUID``/``SETGID``/``KILL`` capabilities):

- each job is a fresh ``python -I -m autoforge.sandbox.runner`` process
  running as the job user, in its own ``<work-dir>/job-<id>`` folder;
- the job is killed after ``protocol.job_seconds`` (all check timeouts, twice);
- afterwards every process of the job user is killed (nothing a check started
  survives into the next job) and the job folder is deleted.

Without ``--job-user`` (local development and tests) jobs run as the current
user and none of the above isolation applies.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from autoforge.sandbox.protocol import MAX_REQUEST_BYTES, decode_request, job_seconds

log = logging.getLogger("autoforge.sandbox")

MAX_JOB_SECONDS = 3600
"""Hard upper bound for one job, whatever the checks' timeouts add up to."""

_CLEANUP = """
import os, shutil, signal, sys
try:
    os.kill(-1, signal.SIGKILL)  # every process of this user except this one
except ProcessLookupError:
    pass
shutil.rmtree(sys.argv[1], ignore_errors=True)
"""


class Sandbox:
    """Runs jobs one at a time in fresh workspaces under ``work_dir``.

    Args:
        work_dir: Where job folders are created (a tmpfs in the container).
        job_user: Run jobs as this user (requires root with SETUID/SETGID/KILL),
            or ``None`` to run them as the current user (development only).
    """

    def __init__(self, work_dir: Path, *, job_user: str | None) -> None:
        self.work_dir = work_dir
        self.job_user = job_user
        self._lock = threading.Lock()

    def run(self, body: bytes) -> tuple[int, dict[str, Any]]:
        """Run one job; return ``(http_status, response_json)``."""
        try:
            manifest, _ = decode_request(body)
        except ValueError as exc:
            return HTTPStatus.BAD_REQUEST, {"error": f"invalid request: {exc}"}
        timeout = min(job_seconds(manifest), MAX_JOB_SECONDS)
        with self._lock:
            job = self.work_dir / f"job-{uuid.uuid4().hex[:12]}"
            log.info("job started: %d check(s), %s", len(manifest["checks"]), job.name)
            try:
                return self._execute(job, body, timeout)
            finally:
                self._cleanup(job)

    def _execute(self, job: Path, body: bytes, timeout: int) -> tuple[int, dict[str, Any]]:
        """Start the runner process and translate its output into a response."""
        cmd = [sys.executable, "-I", "-m", "autoforge.sandbox.runner", str(job)]
        try:
            proc = subprocess.run(cmd, input=body, capture_output=True, timeout=timeout,  # noqa: S603
                                  **self._as_job_user())
        except subprocess.TimeoutExpired:
            log.warning("job timed out after %ds: %s", timeout, job.name)
            return HTTPStatus.GATEWAY_TIMEOUT, {"error": f"job exceeded {timeout} seconds"}
        try:
            result = json.loads(proc.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            stderr = proc.stderr.decode("utf-8", errors="replace")[-2000:]
            return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"runner exited {proc.returncode}: {stderr}"}
        if not isinstance(result, dict) or "checks" not in result:
            error = result.get("error") if isinstance(result, dict) else None
            return HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error or "runner returned no results")}
        log.info("job finished: %s", ", ".join(f"{c['name']}={c['status']}" for c in result["checks"]))
        return HTTPStatus.OK, result

    def _cleanup(self, job: Path) -> None:
        """Kill everything the job left running and delete its folder."""
        if self.job_user is None:
            shutil.rmtree(job, ignore_errors=True)
            return
        subprocess.run([sys.executable, "-I", "-c", _CLEANUP, str(job)], check=False,  # noqa: S603
                       capture_output=True, timeout=60, **self._as_job_user())

    def _as_job_user(self) -> dict[str, Any]:
        """``subprocess`` arguments that drop to the job user (empty without one)."""
        if self.job_user is None:
            return {}
        return {"user": self.job_user, "group": self.job_user, "extra_groups": []}


def make_server(host: str, port: int, sandbox: Sandbox) -> ThreadingHTTPServer:
    """Create the HTTP server (not yet serving) for ``sandbox``."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "autoforge-sandbox"

        def do_GET(self) -> None:
            if self.path == "/healthz":
                self._reply(HTTPStatus.OK, {"status": "ok"})
            else:
                self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/run":
                self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._reply(HTTPStatus.LENGTH_REQUIRED, {"error": "Content-Length required"})
                return
            if not 0 < length <= MAX_REQUEST_BYTES:
                self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": f"body must be 1-{MAX_REQUEST_BYTES} bytes"})
                return
            status, payload = sandbox.run(self.rfile.read(length))
            self._reply(status, payload)

        def _reply(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            log.debug("%s " + format, self.address_string(), *args)

    return ThreadingHTTPServer((host, port), Handler)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and serve until interrupted."""
    parser = argparse.ArgumentParser(prog="autoforge.sandbox.server", description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--job-user", default=None, help="run jobs as this user (needs root + SETUID/SETGID/KILL)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    server = make_server(args.host, args.port, Sandbox(args.work_dir, job_user=args.job_user))
    log.info("sandbox listening on %s:%d (job user: %s)", args.host, args.port, args.job_user or "current user")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
