"""Tests for agents.context: which repository files are selected as LLM context."""

from __future__ import annotations

from autoforge.agents.context import select_context_files
from autoforge.domain.models import RepoFile


def _file(path: str, size: int = 100, mode: str = "100644") -> RepoFile:
    """Build a RepoFile with the given path, size and mode."""
    return RepoFile(path=path, mode=mode, size=size, blob_id=f"b-{path}")


def test_convention_files_first_then_source_within_budget() -> None:
    """README comes first, then source, then other text, within the byte budget; binaries are skipped."""
    files = [_file("docs/guide.md", 50), _file("src/b.py", 300), _file("src/a.c", 200), _file("README.md", 400),
             _file("logo.png", 10)]

    selected = [f.path for f in select_context_files(files, max_context_bytes=700, max_file_bytes=1000)]

    assert selected == ["README.md", "src/a.c", "docs/guide.md"]  # b.py exceeds the remaining budget


def test_skips_oversized_files_and_symlinks() -> None:
    """Files over the per-file limit and symlinks are never selected."""
    files = [_file("big.py", 5000), _file("link.py", 10, mode="120000"), _file("ok.py", 10)]

    selected = [f.path for f in select_context_files(files, max_context_bytes=10_000, max_file_bytes=1000)]

    assert selected == ["ok.py"]
