"""Tests for the verification sandbox: job runner, HTTP server and the service-side SandboxVerifier.

Commands are written to work in both ``/bin/sh`` (CI, the container) and
``cmd`` (local Windows development): plain ``python -c "..."`` with single
quotes inside.
"""

from __future__ import annotations

import io
import sys
import tarfile
import threading
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from autoforge.domain.errors import VerificationError
from autoforge.domain.models import CheckSpec, CodeChange, FileChange
from autoforge.integrations.sandbox.verifier import SandboxVerifier
from autoforge.sandbox import runner
from autoforge.sandbox.protocol import decode_request, encode_request
from autoforge.sandbox.server import Sandbox, make_server

PY = f'"{sys.executable}"'


def _archive(files: dict[str, str], *, link_to: str | None = None) -> bytes:
    """A GitHub-style tarball: everything under one top folder, optionally with a symlink ``link``."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(f"owner-repo-abc123/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if link_to is not None:
            link = tarfile.TarInfo("owner-repo-abc123/link")
            link.type, link.linkname = tarfile.SYMTYPE, link_to
            tar.addfile(link)
    return buf.getvalue()


def _check(name: str, code: str, timeout: int = 60) -> dict[str, object]:
    """A manifest check that runs Python ``code`` (no double quotes inside)."""
    return {"name": name, "command": f'{PY} -c "{code}"', "timeout_seconds": timeout}


def _change(files: dict[str, str | None]) -> CodeChange:
    """A code change writing (or, for ``None``, deleting) ``files``."""
    return CodeChange(summary="s", commit_message="m", pr_title="t", pr_body="b", files=[
        FileChange(path=p, action="delete" if c is None else "modify", content=c or "") for p, c in files.items()])


# ------------------------------------------------------------------ runner


def test_unpack_strips_top_folder_and_skips_escaping_links(tmp_path: Path) -> None:
    """Files land relative to the repository root; a symlink pointing outside is not extracted."""
    runner.unpack(_archive({"src/a.c": "int a;"}, link_to="/etc/passwd"), tmp_path / "t")

    assert (tmp_path / "t" / "src" / "a.c").read_text() == "int a;"
    assert not (tmp_path / "t" / "link").exists() and not (tmp_path / "t" / "link").is_symlink()


@pytest.mark.parametrize("path", ["../x", "/abs", "a\\b", "C:/x", ""])
def test_apply_files_rejects_paths_outside_the_tree(tmp_path: Path, path: str) -> None:
    """The sandbox re-checks paths even though the service's policy already did."""
    with pytest.raises(ValueError, match="path"):
        runner.apply_files(tmp_path, {path: "x"})


def test_apply_files_writes_and_deletes(tmp_path: Path) -> None:
    """New files (with folders) are written, ``None`` deletes."""
    (tmp_path / "old.txt").write_text("x")

    runner.apply_files(tmp_path, {"new/dir/a.txt": "hello", "old.txt": None})

    assert (tmp_path / "new" / "dir" / "a.txt").read_text() == "hello" and not (tmp_path / "old.txt").exists()


def test_run_check_reports_pass_fail_and_timeout(tmp_path: Path) -> None:
    """Exit 0 passes, non-zero fails with its code and output, an endless check times out."""
    (tmp_path / "tree").mkdir()
    ok = runner.run_check(tmp_path / "tree", _check("ok", "print('fine')"), tmp_path)
    bad = runner.run_check(tmp_path / "tree", _check("bad", "import sys; print('boom'); sys.exit(3)"), tmp_path)
    slow = runner.run_check(tmp_path / "tree", _check("slow", "import time; time.sleep(30)", timeout=1), tmp_path)

    assert (ok["status"], ok["exit_code"], ok["output"].strip()) == ("passed", 0, "fine")
    assert (bad["status"], bad["exit_code"]) == ("failed", 3) and "boom" in bad["output"]
    assert slow["status"] == "timeout" and slow["duration_seconds"] < 25


def test_run_check_keeps_only_the_end_of_long_output(tmp_path: Path) -> None:
    """Huge output is truncated to its tail, where compiler errors are."""
    result = runner.run_check(tmp_path, _check("loud", "print('x' * 50000); print('LAST')"), tmp_path)

    assert result["output"].startswith("[... earlier output truncated ...]")
    assert result["output"].rstrip().endswith("LAST") and len(result["output"]) < 7000


def test_run_job_marks_failures_that_exist_on_the_base(tmp_path: Path) -> None:
    """A check broken by the change is a new failure; one that also fails on the base is preexisting."""
    archive = _archive({"value.txt": "ok", "flaky.txt": "broken"})
    manifest = {
        "files": {"value.txt": "changed"},
        "checks": [_check("value", "import sys; sys.exit(0 if open('value.txt').read() == 'ok' else 1)"),
                   _check("flaky", "import sys; sys.exit(0 if open('flaky.txt').read() == 'ok' else 1)"),
                   _check("fine", "print(1)")],
    }

    results = {r["name"]: r for r in runner.run_job(tmp_path / "job", manifest, archive)}

    assert (results["value"]["status"], results["value"]["preexisting"]) == ("failed", False)
    assert (results["flaky"]["status"], results["flaky"]["preexisting"]) == ("failed", True)
    assert results["fine"]["status"] == "passed"


def test_protocol_round_trip_and_validation() -> None:
    """Requests decode to what was encoded; malformed manifests are rejected."""
    manifest = {"checks": [_check("a", "pass")], "files": {"x": None}}
    assert decode_request(encode_request(manifest, b"ARCHIVE")) == (manifest, b"ARCHIVE")
    with pytest.raises(ValueError, match="checks"):
        decode_request(encode_request({"checks": [], "files": {}}, b""))
    with pytest.raises(ValueError, match="timeout"):
        decode_request(encode_request({"checks": [{**_check("a", "pass"), "timeout_seconds": 0}], "files": {}}, b""))


# --------------------------------------------------- server + client, end to end


@pytest.fixture
def sandbox_url(tmp_path: Path) -> Iterator[str]:
    """A real sandbox server on a free localhost port (jobs run as the current user)."""
    server = make_server("127.0.0.1", 0, Sandbox(tmp_path / "work", job_user=None))
    (tmp_path / "work").mkdir()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_verifier_runs_checks_in_the_sandbox(sandbox_url: str, tmp_path: Path) -> None:
    """The service-side client sends archive + change + checks and gets a report; workspaces are removed."""
    archive = _archive({"main.txt": "old"})
    checks = [CheckSpec("exists", f"{PY} -c \"import os, sys; sys.exit(0 if os.path.exists('main.txt') else 4)\""),
              CheckSpec("is-new", f"{PY} -c \"import sys; sys.exit(0 if open('main.txt').read() == 'new' else 1)\"")]
    verifier = SandboxVerifier(sandbox_url, timeout=60)

    report = verifier.verify(archive, _change({"main.txt": "new"}), checks)
    deleted = verifier.verify(archive, _change({"main.txt": None}), checks)

    assert [c.status for c in report.checks] == ["passed", "passed"] and report.passed
    # Deleting breaks "exists" (passes on the base); "is-new" also fails on the base, so it is preexisting.
    assert [(c.status, c.exit_code, c.preexisting) for c in deleted.checks] == [("failed", 4, False),
                                                                                ("failed", 1, True)]
    assert [c.name for c in deleted.new_failures] == ["exists"] and not deleted.passed
    assert list((tmp_path / "work").iterdir()) == []  # every job folder was cleaned up
    assert httpx.get(f"{sandbox_url}/healthz").json() == {"status": "ok"}


def test_verifier_without_checks_skips() -> None:
    """No checks: nothing is sent, the report says why."""
    report = SandboxVerifier("http://127.0.0.1:9", timeout=1).verify(b"", _change({"a": "b"}), [])

    assert report.skipped and report.passed and not report.checks


def test_verifier_reports_sandbox_errors(sandbox_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad archive and an unreachable sandbox both raise VerificationError (the run fails, nothing is published)."""
    with pytest.raises(VerificationError, match="sandbox answered 422"):
        SandboxVerifier(sandbox_url, timeout=30).verify(b"not a tarball", _change({"a": "b"}), [CheckSpec("x", "y")])

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr("autoforge.integrations.sandbox.verifier.time.sleep", lambda _: None)
    unreachable = SandboxVerifier("http://sandbox:8090", timeout=1, transport=httpx.MockTransport(refuse))
    with pytest.raises(VerificationError, match="unreachable"):
        unreachable.verify(b"", _change({"a": "b"}), [CheckSpec("x", "y")])
