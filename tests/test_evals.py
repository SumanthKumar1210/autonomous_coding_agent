"""Tests for the eval harness: suite loading, the local repo host, grading, variants, reports and the CLI.

The real suite in ``evals/`` is loaded here too: every case must be sound
(static part) and context retrieval must find every expected file. That makes
the suite a regression test for ``agents.context`` that runs in CI without an
LLM or a sandbox.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from autoforge.app.cli import main
from autoforge.domain.models import CheckResult, CheckSpec, CodeChange, FileChange, VerificationReport
from autoforge.evals.grading import apply_change, solution_change
from autoforge.evals.local_repo import LocalRepoHost
from autoforge.evals.report import (
    RunRecord,
    aggregate,
    compare,
    load_report,
    markdown_summary,
    prompt_fingerprint,
    report_meta,
    write_report,
)
from autoforge.evals.runner import VARIANTS, EvalAgents, EvalBudget, context_case, run_case, validate_case
from autoforge.evals.suite import HIDDEN_CHECK_PREFIX, EvalCase, SuiteError, load_suite
from autoforge.sandbox.runner import unpack
from tests.fake_agents import FakeCoder, FakeReviewer, reject

SUITE = Path(__file__).resolve().parents[1] / "evals"
BUDGET = EvalBudget()


class RecordingVerifier:
    """Fake sandbox: every check passes unless its name is in ``fail``; records each call."""

    def __init__(self, *fail: str) -> None:
        self.fail = set(fail)
        self.calls: list[tuple[CodeChange, list[CheckSpec]]] = []

    def verify(self, archive: bytes, change: CodeChange, checks: list[CheckSpec]) -> VerificationReport:
        self.calls.append((change, checks))
        return VerificationReport(checks=[
            CheckResult(c.name, c.command, "failed" if c.name in self.fail else "passed",
                        1 if c.name in self.fail else 0, "boom" if c.name in self.fail else "")
            for c in checks])


def _case(case_id: str = "c-soc-clamp") -> EvalCase:
    return load_suite(SUITE, [case_id])[0]


def _solution_coder(case: EvalCase) -> FakeCoder:
    base = LocalRepoHost(case.repo_dir).files
    return FakeCoder(solution_change(case, base).files)


# ------------------------------------------------------------------ the real suite


def test_real_suite_loads() -> None:
    cases = load_suite(SUITE)
    assert {c.id for c in cases} >= {"c-cell-imbalance", "c-pyro-fuse", "c-soc-clamp", "py-energy",
                                     "py-parser-robust"}
    assert {c.repo for c in cases} == {"bms_c", "bms_py"}
    for case in cases:
        assert case.hidden_checks and all(c.name.startswith(HIDDEN_CHECK_PREFIX) for c in case.hidden_checks)
        assert case.ticket.key == case.id and case.ticket.body


@pytest.mark.parametrize("case", load_suite(SUITE), ids=lambda c: c.id)
def test_real_suite_cases_are_sound_statically(case: EvalCase) -> None:
    """The reference solution changes the expected files, nothing protected, and matches every pattern."""
    assert validate_case(case, None) == []


@pytest.mark.parametrize("case", load_suite(SUITE), ids=lambda c: c.id)
def test_context_retrieval_finds_every_expected_file(case: EvalCase) -> None:
    """Regression guard for agents.context: a drop in recall on the suite fails CI."""
    _, score = context_case(case, BUDGET)
    assert score.missing == (), f"context for {case.id} misses {score.missing}"
    assert score.recall == 1.0


def test_hidden_and_solution_files_never_reach_the_agents() -> None:
    case = _case("c-pyro-fuse")
    snapshot, _ = context_case(case, BUDGET)
    assert not any(p.startswith(".autoforge-eval") for p in snapshot.modes)
    assert "src/pyro_fuse.c" not in snapshot.modes


# ------------------------------------------------------------------ suite loading


def _write_suite(root: Path, case_toml: str, *, hidden: bool = True) -> Path:
    (root / "repos" / "r").mkdir(parents=True)
    (root / "repos" / "r" / "a.py").write_text("x = 1\n")
    case = root / "cases" / "c1"
    case.mkdir(parents=True)
    (case / "case.toml").write_text(case_toml)
    if hidden:
        (case / "hidden").mkdir()
        (case / "hidden" / "t.py").write_text("")
    return root


VALID = """repo = "r"
title = "T"
body = "B"
[expect]
context = ["a.py"]
changed = ["a.py"]
[[hidden_checks]]
name = "acc"
run = "true"
"""


def test_minimal_suite_loads(tmp_path: Path) -> None:
    (case,) = load_suite(_write_suite(tmp_path, VALID))
    assert case.hidden_checks[0] == CheckSpec("hidden: acc", "true", 600)
    assert case.hidden_files == {"t.py": ""}


@pytest.mark.parametrize(("toml", "message"), [
    (VALID + "surprise = 1\n", "unknown keys"),
    (VALID.replace('repo = "r"', 'repo = "nope"'), "not found"),
    (VALID.replace('context = ["a.py"]', 'context = ["missing.py"]'), "do not exist"),
    (VALID.replace('changed = ["a.py"]', 'changed = ["a.py"]\nunchanged = ["a.py"]'), "both changed and unchanged"),
    (VALID.replace('changed = ["a.py"]', "contains = [{ path = \"a.py\", pattern = \"(\" }]"), "invalid pattern"),
    (VALID.replace('run = "true"', 'run = "true"\ntimeout = 99999'), "timeout"),
    (VALID.replace('title = "T"', 'title = ""'), "'title'"),
    ("repo = ", "cannot read"),
])
def test_invalid_cases_are_rejected(tmp_path: Path, toml: str, message: str) -> None:
    with pytest.raises(SuiteError, match=message):
        load_suite(_write_suite(tmp_path, toml))


def test_hidden_checks_need_hidden_files(tmp_path: Path) -> None:
    with pytest.raises(SuiteError, match="need files"):
        load_suite(_write_suite(tmp_path, VALID, hidden=False))


def test_unknown_case_id_is_rejected() -> None:
    with pytest.raises(SuiteError, match="unknown case"):
        load_suite(SUITE, ["no-such-case"])


# ------------------------------------------------------------------ local repo host


def test_local_repo_archive_unpacks_in_the_sandbox_runner(tmp_path: Path) -> None:
    case = _case()
    repo = LocalRepoHost(case.repo_dir)
    dest = tmp_path / "head"
    unpack(repo.download_archive(repo.get_base()), dest)
    unpacked = {p.relative_to(dest).as_posix(): p.read_text() for p in dest.rglob("*") if p.is_file()}
    assert unpacked == repo.files


def test_local_repo_is_read_only_and_only_records_publishing() -> None:
    case = _case()
    repo = LocalRepoHost(case.repo_dir)
    base = repo.get_base()
    assert base == LocalRepoHost(case.repo_dir).get_base()  # content-addressed, stable
    files = repo.list_files(base)
    assert {f.path for f in files} == set(repo.files)
    assert repo.read_files(base, files[:1]) == {files[0].path: repo.files[files[0].path].encode()}
    assert repo.find_open_pull("autoforge/x") is None
    with tarfile.open(fileobj=io.BytesIO(repo.download_archive(base))) as tar:
        assert all(m.isreg() for m in tar.getmembers())


# ------------------------------------------------------------------ running cases


def test_full_variant_with_reference_solution_is_resolved() -> None:
    case = _case()
    verifier = RecordingVerifier()
    run = run_case(case, VARIANTS["full"], EvalAgents(_solution_coder(case), FakeReviewer(), verifier), BUDGET)
    assert run.outcome == "completed" and run.resolved
    assert run.attempts == 1 and run.usage.input_tokens == 140  # coder 100 + reviewer 40
    assert run.context is not None and run.context.recall == 1.0
    assert run.change is not None and run.change.scope_ok
    graded_change, graded_checks = verifier.calls[-1]  # the grading call: repo checks + hidden checks
    assert [c.name for c in graded_checks] == ["cmake: .", "hidden: acceptance"]
    assert ".autoforge-eval/acceptance.c" in {f.path for f in graded_change.files}
    assert len(verifier.calls) == 2  # once in the loop, once for grading


def test_failing_hidden_check_means_unresolved() -> None:
    case = _case()
    run = run_case(case, VARIANTS["no-review"],
                   EvalAgents(_solution_coder(case), FakeReviewer(), RecordingVerifier("hidden: acceptance")), BUDGET)
    assert run.outcome == "completed" and not run.resolved
    assert run.change is not None and run.change.hidden_total == 1 and run.change.hidden_passed == 0
    assert RunRecord.from_run(run).failed_checks == ["hidden: acceptance (failed): boom"]


def test_single_variant_runs_no_checks_or_reviewer_in_the_loop() -> None:
    case = _case()
    verifier = RecordingVerifier()
    reviewer = FakeReviewer(reject())
    run = run_case(case, VARIANTS["single"], EvalAgents(_solution_coder(case), reviewer, verifier), BUDGET)
    assert run.outcome == "completed" and run.resolved
    assert reviewer.reviewed == [] and run.reviewer_model == ""
    assert len(verifier.calls) == 1  # grading only


def test_review_rejection_is_graded_and_counted_as_false_reject() -> None:
    case = _case()
    coder = _solution_coder(case)
    run = run_case(case, VARIANTS["full"], EvalAgents(coder, FakeReviewer(reject()), RecordingVerifier()),
                   EvalBudget(max_attempts=2))
    assert run.outcome == "review_rejected" and run.attempts == 2 and len(coder.calls) == 2
    assert run.resolved  # the change was right; the reviewer was wrong
    assert aggregate([RunRecord.from_run(run)])["full"].false_rejects == 1


def test_policy_violation_is_reported_and_not_verified() -> None:
    case = _case()
    coder = FakeCoder([FileChange(path="../escape.c", action="create", content="x")])
    verifier = RecordingVerifier()
    run = run_case(case, VARIANTS["single"], EvalAgents(coder, FakeReviewer(), verifier), BUDGET)
    assert run.outcome == "policy_failed" and not run.resolved
    assert run.change is not None and run.change.policy_error and run.change.checks_skipped
    assert verifier.calls == []


def test_scope_graders_catch_wrong_files() -> None:
    case = _case()
    base = LocalRepoHost(case.repo_dir).files
    coder = FakeCoder([FileChange(path="include/soc.h", action="modify", content=base["include/soc.h"] + "\n")])
    run = run_case(case, VARIANTS["single"], EvalAgents(coder, FakeReviewer(), RecordingVerifier()), BUDGET)
    assert run.change is not None and not run.change.scope_ok
    assert run.change.changed_missing == ("src/soc.c", "tests/test_soc.c")
    assert run.change.unchanged_violations == ("include/soc.h",)


def test_agent_error_without_change_has_no_grade() -> None:
    from autoforge.domain.errors import AgentError

    case = _case()
    run = run_case(case, VARIANTS["full"],
                   EvalAgents(FakeCoder(error=AgentError("refused")), FakeReviewer(), RecordingVerifier()), BUDGET)
    assert run.outcome == "agent_error" and run.change is None and not run.resolved
    assert run.context is not None  # the context was built before the coder failed


def test_without_sandbox_nothing_counts_as_resolved() -> None:
    case = _case()
    run = run_case(case, VARIANTS["full"], EvalAgents(_solution_coder(case), FakeReviewer(), None), BUDGET)
    assert run.outcome == "completed" and not run.resolved
    assert run.change is not None and "no verification sandbox" in run.change.checks_skipped
    assert aggregate([RunRecord.from_run(run)])["full"].unverified == 1


def test_validate_case_with_sandbox_checks_both_directions() -> None:
    case = _case()
    # Hidden check "passes" on the untouched fixture too: the case would test nothing.
    problems = validate_case(case, RecordingVerifier())
    assert problems == ["hidden: acceptance already passes on the untouched fixture, so it tests nothing"]
    assert any("solution fails hidden: acceptance" in p
               for p in validate_case(case, RecordingVerifier("hidden: acceptance")))


def test_apply_change() -> None:
    change = CodeChange(summary="", commit_message="", pr_title="", pr_body="", files=[
        FileChange(path="a", action="modify", content="A"), FileChange(path="b", action="delete", content=""),
        FileChange(path="c", action="create", content="C")])
    assert apply_change({"a": "a", "b": "b"}, change) == {"a": "A", "c": "C"}


# ------------------------------------------------------------------ reports


def _record(variant: str, resolved: bool, **extra: object) -> RunRecord:
    values: dict[str, object] = {"case_id": "c", "variant": variant, "repeat": 1, "run_id": "r",
                                 "outcome": "completed", "resolved": resolved, "has_change": True,
                                 "input_tokens": 100, "output_tokens": 10, "context_recall": 1.0}
    values.update(extra)
    return RunRecord(**values)  # type: ignore[arg-type]


def test_report_round_trip_aggregate_and_markdown(tmp_path: Path) -> None:
    records = [_record("no-review", False, failed_checks=["hidden: acceptance (failed): x"]),
               _record("full", True, input_tokens=300)]
    meta = report_meta(coder_model="m1", reviewer_model="m2", budget={"max_attempts": 3}, suite="evals",
                       started_at="2026-11-17T00:00:00+00:00")
    path = tmp_path / "r.json"
    write_report(path, meta, records)
    loaded_meta, loaded = load_report(path)
    assert loaded == records and loaded_meta["prompt_fingerprint"] == prompt_fingerprint()

    aggs = aggregate(loaded)
    assert aggs["full"].resolved_rate == 1.0 and aggs["no-review"].resolved_rate == 0.0
    assert aggs["no-review"].false_accepts == 1  # approved (no reviewer) but wrong
    text = markdown_summary(meta, loaded)
    assert "**Reviewer lift** (full − no-review): +100 pp resolved, +200 tokens per run." in text
    assert "| c | 0/1 (completed, 1 check(s) failed) | 1/1  |" in text


def test_compare_reports_deltas() -> None:
    old = ({"coder_model": "a"}, [_record("full", False)])
    new = ({"coder_model": "b"}, [_record("full", True), _record("single", True)])
    text = compare(old, new)
    assert "| coder_model | a | b |" in text
    assert "| full | 1 → 1 | 0% → 100% (+100 pp) |" in text
    assert "| single | only in new report |" in text
    assert "- c / full: 0% → 100% resolved" in text


def test_load_report_rejects_other_files(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text('{"meta": {"schema": 99}, "records": []}')
    with pytest.raises(ValueError, match="schema"):
        load_report(path)


# ------------------------------------------------------------------ CLI


def test_cli_eval_context_and_validate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("SANDBOX_URL", raising=False)
    assert main(["eval", "context", "--suite", str(SUITE), "--case", "c-soc-clamp", "--verbose"]) == 0
    out = capsys.readouterr().out
    assert "| c-soc-clamp | 100% |" in out and "src/soc.c:" in out
    assert main(["eval", "validate", "--suite", str(SUITE), "--case", "c-soc-clamp"]) == 0
    assert "ok  c-soc-clamp" in capsys.readouterr().out


def test_cli_eval_run_needs_openai_settings_and_a_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "SANDBOX_URL"):
        monkeypatch.delenv(name, raising=False)
    assert main(["eval", "run", "--suite", str(SUITE)]) == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "m")
    assert main(["eval", "run", "--suite", str(SUITE)]) == 2  # no SANDBOX_URL, no --no-sandbox
    assert "SANDBOX_URL" in capsys.readouterr().err


def test_cli_eval_bad_suite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["eval", "context", "--suite", str(tmp_path)]) == 2
    assert "no cases folder" in capsys.readouterr().err
