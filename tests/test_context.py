"""Tests for agents.context: which repository files are selected as LLM context, and why."""

from __future__ import annotations

from autoforge.agents.context import (
    TicketTerms,
    extract_terms,
    make_excerpt,
    plan_context,
    select_context_files,
)
from autoforge.domain.models import RepoFile, Ticket


def _file(path: str, size: int = 100, mode: str = "100644") -> RepoFile:
    """Build a RepoFile with the given path, size and mode."""
    return RepoFile(path=path, mode=mode, size=size, blob_id=f"b-{path}")


def _ticket(title: str, body: str = "", comments: list[str] | None = None) -> Ticket:
    """Build a ticket with the given text."""
    return Ticket(key="1", title=title, body=body, url="", labels=[], comments=comments or [])


REPO: dict[str, str] = {
    "README.md": "# Pack\n",
    "fw/CMakeLists.txt": "add_library(bms src/can.c src/fault.c)\n",
    "fw/include/bms/types.h": "typedef enum { BMS_OK } bms_status_t;\n",
    "fw/include/bms/config.h": "#define BMS_CELL_OV_MV (4200U)\n#define BMS_CAN_ID (0x3A0U)\n",
    "fw/include/bms/can.h": '#include "bms/types.h"\nbms_status_t can_encode_status(int x);\n',
    "fw/src/can.c": '#include "bms/can.h"\n#include "bms/config.h"\nbms_status_t can_encode_status(int x)\n{\n}\n',
    "fw/include/bms/fault.h": '#include "bms/types.h"\nbms_status_t fault_update(void);\n',
    "fw/src/fault.c": '#include "bms/fault.h"\n#include "bms/config.h"\nbms_status_t fault_update(void)\n{\n}\n',
    "fw/src/app.c": '#include "bms/can.h"\n#include "bms/fault.h"\nvoid app_step(void) { can_encode_status(1); }\n',
    "fw/test/test_can.c": '#include "bms/can.h"\nint main(void) { return can_encode_status(0); }\n',
    "tools/plot.py": "import matplotlib\n",
    "logo.png": "binary",
}


def _plan(ticket: Ticket, *, files: dict[str, str] = REPO, budget: int = 60_000, max_file: int = 20_000):
    """Run plan_context on an in-memory repo."""
    repo_files = [_file(p, len(c.encode())) for p, c in files.items()]
    return plan_context(repo_files, dict(files), extract_terms(ticket), max_context_bytes=budget,
                        max_file_bytes=max_file)


def test_extract_terms() -> None:
    """Paths, identifiers (snake/camel case, calls, backticks) and keywords are extracted; filler words dropped."""
    terms = extract_terms(_ticket("Fix `can_encode_status` in src/can.c",
                                  "The SocEstimator should call update_soc() when the CAN frame is late.",
                                  comments=["Also check degC units in BMS_CELL_OV_MV"]))

    assert "src/can.c" in terms.paths
    assert {"can_encode_status", "SocEstimator", "update_soc", "BMS_CELL_OV_MV"} <= terms.identifiers
    assert "degC" not in terms.identifiers  # a unit, not code
    assert {"frame", "late", "can", "soc", "estimator"} <= terms.keywords  # "CAN" kept as an acronym
    assert not {"the", "should", "when", "fix"} & terms.keywords


def test_ticket_naming_a_function_selects_its_module_and_neighbours() -> None:
    """The defining source, its header, includes, unit test, a caller and the build file are chosen, with reasons."""
    files, excerpts, context = _plan(_ticket("Status frame wrong", "`can_encode_status` sends the wrong ID."))
    reasons = {c.path: c.reason for c in context}

    assert reasons["fw/src/can.c"].startswith("defines `can_encode_status`")
    assert reasons["fw/include/bms/can.h"].startswith("defines `can_encode_status`")
    assert reasons["fw/include/bms/config.h"] == "included by fw/src/can.c"
    assert reasons["fw/include/bms/types.h"] == "included by fw/include/bms/can.h"
    assert reasons["fw/test/test_can.c"].startswith("unit test of fw/")
    assert reasons["fw/src/app.c"].startswith("uses ")
    assert reasons["fw/CMakeLists.txt"] == "build/convention file"
    assert reasons["README.md"] == "build/convention file"
    assert "fw/src/fault.c" not in files and "tools/plot.py" not in files and "logo.png" not in files
    assert not excerpts
    assert [c.path for c in context][:2] == ["README.md", "fw/CMakeLists.txt"]  # convention files first


def test_keyword_ticket_selects_by_path() -> None:
    """Without identifiers, a keyword that matches a module's path is enough to find it."""
    files, _, _ = _plan(_ticket("Fault debounce is too short"))

    assert "fw/src/fault.c" in files and "fw/include/bms/fault.h" in files
    assert "fw/src/can.c" not in files


def test_explicit_path_mention_wins() -> None:
    """A file named in the ticket is selected even with no other match."""
    _, _, context = _plan(_ticket("Rewrite tools/plot.py"))

    assert ("tools/plot.py", "named in the ticket") in {(c.path, c.reason.split(";")[0]) for c in context}


def test_no_match_falls_back_to_general_sample() -> None:
    """A ticket that matches nothing gets the general sample (convention files and small sources first)."""
    files, _, context = _plan(_ticket("Make it nicer"))

    assert "README.md" in files and all(c.reason == "general context (no ticket match)" for c in context)


def test_large_relevant_file_becomes_excerpt() -> None:
    """A relevant file over the per-file limit is shown as an excerpt, marked as such."""
    big = "#include \"bms/can.h\"\n" + "".join(f"static int pad_{i}(void) {{ return {i}; }}\n" for i in range(400))
    big += "bms_status_t can_encode_status(int x)\n{\n    return BMS_OK;\n}\n"
    files, excerpts, context = _plan(_ticket("`can_encode_status` bug"), files={**REPO, "fw/src/can.c": big},
                                     max_file=2_000)

    assert "fw/src/can.c" not in files and "fw/src/can.c" in excerpts
    assert "bms_status_t can_encode_status(int x)" in excerpts["fw/src/can.c"]
    assert "omitted" in excerpts["fw/src/can.c"]
    assert next(c for c in context if c.path == "fw/src/can.c").excerpt


def test_budget_is_respected_in_priority_order() -> None:
    """When the budget is small, lower-priority files (callers) are dropped before the module itself."""
    files, excerpts, _ = _plan(_ticket("`can_encode_status` bug"), budget=200)

    assert "fw/src/can.c" in files or "fw/src/can.c" in excerpts
    assert "fw/src/app.c" not in files
    assert sum(len(v) for v in files.values()) + sum(len(v) for v in excerpts.values()) <= 200


def test_make_excerpt_keeps_head_and_identifier_windows() -> None:
    """Excerpts keep the file head and lines near ticket identifiers, and mark the gaps."""
    lines = [f"line {i}" for i in range(200)]
    lines[120] = "int target_fn(void)"
    text = make_excerpt("\n".join(lines), TicketTerms(identifiers=frozenset({"target_fn"})))

    assert "line 0" in text and "int target_fn(void)" in text and "line 113" in text
    assert "line 60" not in text
    assert "... [lines 26-112 omitted] ..." in text and text.endswith("... [lines 130-200 omitted] ...")


def test_fallback_convention_files_first_then_source_within_budget() -> None:
    """README comes first, then source, then other text, within the byte budget; binaries are skipped."""
    files = [_file("docs/guide.md", 50), _file("src/b.py", 300), _file("src/a.c", 200), _file("README.md", 400),
             _file("logo.png", 10)]

    selected = [f.path for f in select_context_files(files, max_context_bytes=700, max_file_bytes=1000)]

    assert selected == ["README.md", "src/a.c", "docs/guide.md"]  # b.py exceeds the remaining budget


def test_fallback_skips_oversized_files_and_symlinks() -> None:
    """Files over the per-file limit and symlinks are never selected."""
    files = [_file("big.py", 5000), _file("link.py", 10, mode="120000"), _file("ok.py", 10)]

    selected = [f.path for f in select_context_files(files, max_context_bytes=10_000, max_file_bytes=1000)]

    assert selected == ["ok.py"]
