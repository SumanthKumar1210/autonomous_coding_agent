"""Repository context for agents: which files the LLM gets to read.

LLM context is limited and costs money, so AutoForge does not send the whole
repository. Instead it sends:

1. the full file *tree* (all paths), so the agent knows the structure; and
2. the *content* of a selection of files, within a byte budget.

Selection today is deterministic: top-level convention files first (README,
pyproject.toml, CMakeLists.txt, ...), then source files, then other text
files, smallest first. A smarter retrieval strategy (Phase 1, increment 4)
only needs to replace ``select_context_files``.
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
"""File names (lower-case) that describe how the project is built and styled."""

_SOURCE_SUFFIXES = {".py", ".pyi", ".c", ".h", ".cpp", ".hpp"}
"""Source-code extensions, preferred over other text files."""

_TEXT_SUFFIXES = _SOURCE_SUFFIXES | {".toml", ".cfg", ".ini", ".md", ".rst", ".txt", ".yml", ".yaml", ".json", ".cmake"}
"""All extensions considered text and therefore eligible for the context."""

_SYMLINK_MODE = "120000"
"""Git file mode for symbolic links (never included: content is just a target path)."""


def build_snapshot(repo: RepoHost, *, max_context_bytes: int, max_file_bytes: int) -> RepoSnapshot:
    """Build the ``RepoSnapshot`` the agents will work from.

    Steps: get the default-branch head, list all files, choose which ones to
    read (see ``select_context_files``), download and decode them as UTF-8.
    Files that turn out not to be valid UTF-8 (binary) are skipped.

    Args:
        repo: The repository to read from.
        max_context_bytes: Total byte budget for file contents shown to the LLM.
        max_file_bytes: Files larger than this are never included.

    Returns:
        A snapshot with the base commit, every path's mode, and the selected contents.
    """
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
    """Choose which files' contents to show the LLM.

    Pure function (no I/O), so it is easy to test and to swap for a smarter one.

    Rules:
        - only text files (by name/extension), never symlinks;
        - skip files larger than ``max_file_bytes``;
        - order by priority (convention files, then source, then other text),
          then by size (small first), then by path (stable order);
        - add files while they fit in ``max_context_bytes``.

    Args:
        files: Every file in the repository.
        max_context_bytes: Total byte budget.
        max_file_bytes: Per-file size limit.

    Returns:
        The selected files, in priority order.
    """
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
    """Return True if the path looks like a text file worth showing the LLM."""
    p = PurePosixPath(path)
    return p.name.lower() in _CONVENTION_FILES or p.suffix.lower() in _TEXT_SUFFIXES


def _rank(path: str) -> int:
    """Priority of a file for the context (lower = more important).

    0 = top-level convention file (README, pyproject.toml, ...),
    1 = source code, 2 = any other text file.
    """
    p = PurePosixPath(path)
    if p.name.lower() in _CONVENTION_FILES and len(p.parts) == 1:
        return 0
    if p.suffix.lower() in _SOURCE_SUFFIXES:
        return 1
    return 2
