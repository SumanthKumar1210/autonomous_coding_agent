"""Repository context for agents.

Deterministic selection (convention files first, then small source files)
within a byte budget. Smarter retrieval is a later Phase 1 increment and
replaces `select_context_files` only.
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

from autoforge.domain.models import RepoFile, RepoSnapshot
from autoforge.ports import RepoHost

log = logging.getLogger(__name__)

_CONVENTION_FILES = {
    "readme.md", "readme.rst", "readme", "contributing.md", "pyproject.toml", "setup.cfg",
    "setup.py", "requirements.txt", "makefile", "cmakelists.txt", ".clang-format", "ruff.toml",
    ".editorconfig",
}
_SOURCE_SUFFIXES = {".py", ".pyi", ".c", ".h", ".cpp", ".hpp"}
_TEXT_SUFFIXES = _SOURCE_SUFFIXES | {".toml", ".cfg", ".ini", ".md", ".rst", ".txt", ".yml", ".yaml", ".json", ".cmake"}
_SYMLINK_MODE = "120000"


def build_snapshot(repo: RepoHost, *, max_context_bytes: int, max_file_bytes: int) -> RepoSnapshot:
    base = repo.get_base()
    all_files = repo.list_files(base)
    files: dict[str, str] = {}
    for f in select_context_files(all_files, max_context_bytes=max_context_bytes, max_file_bytes=max_file_bytes):
        try:
            files[f.path] = repo.read_file(f).decode("utf-8")
        except UnicodeDecodeError:
            continue
    log.info("repo snapshot built", extra={"branch": base.branch, "commit": base.commit_sha,
                                           "files_total": len(all_files), "files_in_context": len(files)})
    return RepoSnapshot(base=base, modes={f.path: f.mode for f in all_files}, files=files)


def select_context_files(files: list[RepoFile], *, max_context_bytes: int, max_file_bytes: int) -> list[RepoFile]:
    candidates = [f for f in files if f.mode != _SYMLINK_MODE and f.size <= max_file_bytes and _is_text(f.path)]
    candidates.sort(key=lambda f: (_rank(f.path), f.size, f.path))
    selected: list[RepoFile] = []
    used = 0
    for f in candidates:
        if used + f.size <= max_context_bytes:
            selected.append(f)
            used += f.size
    return selected


def _is_text(path: str) -> bool:
    p = PurePosixPath(path)
    return p.name.lower() in _CONVENTION_FILES or p.suffix.lower() in _TEXT_SUFFIXES


def _rank(path: str) -> int:
    p = PurePosixPath(path)
    if p.name.lower() in _CONVENTION_FILES and len(p.parts) == 1:
        return 0
    if p.suffix.lower() in _SOURCE_SUFFIXES:
        return 1
    return 2
