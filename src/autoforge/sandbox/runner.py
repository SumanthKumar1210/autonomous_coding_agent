"""One sandbox job: unpack the repository, apply the change, run the checks.

Started by ``sandbox.server`` as a separate process running as the
unprivileged job user::

    python -I -m autoforge.sandbox.runner /work/job-<id>   < request body   > JSON result

1. Unpack the base archive into ``<workspace>/head`` (regular files, folders
   and in-tree symlinks only; size-capped) and apply the change's files.
2. Run every check there with a minimal environment, its own process group,
   a timeout, and only the end of its output kept.
3. For each check that did not pass, unpack a pristine copy into
   ``<workspace>/base`` and run it there too: if it also fails on the base
   commit, the failure is ``preexisting`` and not blamed on the change.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path, PurePosixPath
from typing import IO, Any

from autoforge.sandbox.protocol import decode_request

OUTPUT_TAIL_BYTES = 6000
"""Bytes of combined stdout/stderr kept per check (the end, where errors are)."""

MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
"""Largest repository size after unpacking (guards against archive bombs)."""


def run_job(workspace: Path, manifest: dict[str, Any], archive: bytes) -> list[dict[str, Any]]:
    """Run the manifest's checks on the changed repository; mark failures that already exist on the base.

    Returns:
        One result dict per check (see ``sandbox.protocol``).
    """
    head = workspace / "head"
    unpack(archive, head)
    apply_files(head, manifest["files"])
    checks: list[dict[str, Any]] = manifest["checks"]
    results = [run_check(head, c, workspace) for c in checks]
    failed = [i for i, r in enumerate(results) if r["status"] != "passed"]
    if failed:
        base = workspace / "base"
        unpack(archive, base)
        for i in failed:
            results[i]["preexisting"] = run_check(base, checks[i], workspace)["status"] != "passed"
    return results


def unpack(archive: bytes, dest: Path) -> None:
    """Extract a GitHub tarball into ``dest``, dropping its top-level folder.

    Only regular files, folders and symlinks that stay inside ``dest`` are
    extracted (tarfile's ``data`` filter); devices, hard links and escaping
    links are skipped.

    Raises:
        ValueError: If the unpacked size exceeds ``MAX_UNPACKED_BYTES``.
    """
    dest.mkdir(parents=True)
    total = 0
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar:
            _, _, name = member.name.partition("/")
            if not name or not (member.isreg() or member.isdir() or member.issym()):
                continue
            total += member.size
            if total > MAX_UNPACKED_BYTES:
                raise ValueError(f"repository exceeds {MAX_UNPACKED_BYTES} bytes unpacked")
            renamed = copy.copy(member)
            renamed.name = name
            try:
                tar.extract(renamed, dest, filter="data")
            except tarfile.FilterError:
                continue


def apply_files(tree: Path, files: dict[str, str | None]) -> None:
    """Write (or, for ``None``, delete) the change's files inside ``tree``.

    Raises:
        ValueError: If a path is not a clean relative path inside the tree.
    """
    root = tree.resolve()
    for path, content in files.items():
        p = PurePosixPath(path)
        if not path or p.is_absolute() or ".." in p.parts or "\\" in path or ":" in path:
            raise ValueError(f"invalid path in change: {path!r}")
        target = tree.joinpath(*p.parts)
        if target.is_symlink():  # never write through a link
            target.unlink()
        if content is None:
            if target.is_file():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.resolve().is_relative_to(root):
            raise ValueError(f"path leaves the repository: {path!r}")
        target.write_bytes(content.encode("utf-8"))


def run_check(tree: Path, check: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """Run one check's shell command in ``tree`` and return its result dict."""
    home = workspace / "home"
    tmp = workspace / "tmp"
    home.mkdir(exist_ok=True)
    tmp.mkdir(exist_ok=True)
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(home), "TMPDIR": str(tmp),
           "LANG": "C.UTF-8", "CI": "true", "PYTHONDONTWRITEBYTECODE": "1"}
    if sys.platform == "win32":  # local development only; the sandbox container is Linux
        env.update({k: os.environ[k] for k in ("SYSTEMROOT", "COMSPEC") if k in os.environ})
    result: dict[str, Any] = {"name": check["name"], "command": check["command"], "exit_code": None,
                              "preexisting": False}
    start = time.monotonic()
    try:
        proc = _spawn(check["command"], tree, env)
    except OSError as exc:
        return {**result, "status": "error", "output": f"could not start: {exc}", "duration_seconds": 0.0}
    assert proc.stdout is not None  # noqa: S101 - stdout=PIPE above
    tail = _Tail(proc.stdout)
    try:
        proc.wait(timeout=check["timeout_seconds"])
        status = "passed" if proc.returncode == 0 else "failed"
        result["exit_code"] = proc.returncode
    except subprocess.TimeoutExpired:
        status = "timeout"
    _kill(proc)  # also after a normal exit: nothing the check started in the background may keep running
    tail.join(timeout=2)  # a process that escaped the group may hold the pipe; the job cleanup kills it
    result.update(status=status, output=tail.text(), duration_seconds=round(time.monotonic() - start, 2))
    return result


def _spawn(command: str, cwd: Path, env: dict[str, str]) -> subprocess.Popen[bytes]:
    """Start ``command`` under the shell in its own process group, output to one pipe."""
    common: dict[str, Any] = {"cwd": cwd, "env": env, "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
                              "stderr": subprocess.STDOUT}
    if sys.platform == "win32":  # local development only
        proc = subprocess.Popen(command, shell=True, **common)  # noqa: S602 - the sandbox runs repository checks
    else:
        proc = subprocess.Popen(["/bin/sh", "-c", command], start_new_session=True, **common)  # noqa: S603
    return proc


def _kill(proc: subprocess.Popen[bytes]) -> None:
    """Kill the check and everything still running in its process group."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if sys.platform == "win32":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10)


class _Tail:
    """Reads a pipe to the end in a background thread, keeping only the last bytes."""

    def __init__(self, pipe: IO[bytes]) -> None:
        self._pipe = pipe
        self._buf = bytearray()
        self._truncated = False
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        fd = self._pipe.fileno()
        while chunk := os.read(fd, 65536):  # returns what is available (a buffered read would wait for 64 KiB)
            self._buf += chunk
            if len(self._buf) > OUTPUT_TAIL_BYTES:
                del self._buf[:-OUTPUT_TAIL_BYTES]
                self._truncated = True

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)

    def text(self) -> str:
        text = bytes(self._buf).decode("utf-8", errors="replace")
        return f"[... earlier output truncated ...]\n{text}" if self._truncated else text


def main(argv: list[str] | None = None) -> int:
    """Entry point: read the request from stdin, print ``{"checks": [...]}`` or ``{"error": ...}``."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(json.dumps({"error": "usage: python -m autoforge.sandbox.runner WORKSPACE"}))
        return 2
    try:
        manifest, archive = decode_request(sys.stdin.buffer.read())
        results = run_job(Path(args[0]), manifest, archive)
    except Exception as exc:  # report every failure to the server as JSON
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps({"checks": results}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
