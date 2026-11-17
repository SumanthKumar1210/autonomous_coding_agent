"""``autoforge eval``: run the evaluation suite (see ``autoforge.evals`` and ``evals/README.md``).

Usage::

    autoforge eval context  [--suite evals] [--case ID ...] [--verbose] [--min-recall 1.0]
    autoforge eval validate [--suite evals] [--case ID ...]
    autoforge eval run      [--suite evals] [--case ID ...] [--variant full ...] [--repeat N] [--out DIR]
    autoforge eval compare  OLD.json NEW.json

- ``context``: free and deterministic; scores the context retrieval (also runs in CI).
- ``validate``: proves every case is sound (hidden checks fail on the fixture,
  pass with the reference solution). Needs ``SANDBOX_URL`` for the checks; no LLM.
- ``run``: runs the agents on every case in every variant, grades the changes,
  writes ``<out>/eval-<time>.json`` and ``.md``, and prints the Markdown summary.
  **Spends OpenAI tokens.** Needs ``OPENAI_*`` and ``SANDBOX_URL``; never GitHub.
- ``compare``: compares two ``run`` reports (e.g. before and after a prompt change).

Exit codes: 0 success; 1 a context case below ``--min-recall`` or an unsound
case; 2 configuration or suite error.
"""

from __future__ import annotations

import argparse
import io
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from autoforge.app.config import AgentSettings, RunSettings
from autoforge.app.wiring import build_eval_agents, build_verifier, eval_budget
from autoforge.evals.report import (
    RunRecord,
    compare,
    load_report,
    markdown_summary,
    report_meta,
    write_report,
)
from autoforge.evals.runner import VARIANTS, context_case, run_case, validate_case
from autoforge.evals.suite import EvalCase, SuiteError, load_suite
from autoforge.observability import configure_logging

DEFAULT_SUITE = Path("evals")
DEFAULT_OUT = Path(".autoforge/evals")


def add_eval_parser(sub: Any) -> None:
    """Register ``autoforge eval`` and its commands on the top-level subparsers."""
    ev = sub.add_parser("eval", help="Measure output quality on the evaluation suite (evals/)")
    cmds = ev.add_subparsers(dest="eval_command", required=True)

    def suite_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--suite", type=Path, default=DEFAULT_SUITE, help="Suite folder (default: evals)")
        p.add_argument("--case", action="append", default=[], metavar="ID", help="Only this case (repeatable)")

    ctx = cmds.add_parser("context", help="Score context retrieval (no LLM, no sandbox)")
    suite_args(ctx)
    ctx.add_argument("--verbose", action="store_true", help="List every context file and why it was chosen")
    ctx.add_argument("--min-recall", type=float, default=1.0, help="Fail if a case's recall is below this")

    val = cmds.add_parser("validate", help="Check every case is sound (sandbox, no LLM)")
    suite_args(val)

    run = cmds.add_parser("run", help="Run the agents on the suite and grade them (spends OpenAI tokens)")
    suite_args(run)
    run.add_argument("--variant", action="append", default=[], choices=sorted(VARIANTS),
                     help="Variant to run (repeatable; default: all)")
    run.add_argument("--repeat", type=int, default=1, help="Runs per case and variant (LLMs vary)")
    run.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Report folder (default: .autoforge/evals)")
    run.add_argument("--no-sandbox", action="store_true",
                     help="Run without SANDBOX_URL: nothing is verified, so nothing counts as resolved")

    cmp = cmds.add_parser("compare", help="Compare two run reports")
    cmp.add_argument("old", type=Path)
    cmp.add_argument("new", type=Path)


def run_eval(args: argparse.Namespace) -> int:
    """Run the chosen ``autoforge eval`` command; return the exit code."""
    for stream in (sys.stdout, sys.stderr):  # reports use →, − and –; Windows consoles default to cp1252
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        if args.eval_command == "compare":
            print(compare(load_report(args.old), load_report(args.new)), end="")
            return 0
        cases = load_suite(args.suite, args.case)
    except (SuiteError, ValueError, OSError) as exc:
        print(f"Eval error: {exc}", file=sys.stderr)
        return 2
    try:
        settings: RunSettings = AgentSettings() if args.eval_command == "run" else RunSettings()
    except ValidationError as exc:
        missing = ", ".join(".".join(str(p) for p in e["loc"]).upper() for e in exc.errors())
        print(f"Configuration error (check .env): {missing}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level if args.eval_command != "context" else "WARNING")
    if args.eval_command == "context":
        return _context(cases, settings, args.verbose, args.min_recall)
    if args.eval_command == "validate":
        return _validate(cases, settings)
    assert isinstance(settings, AgentSettings)  # noqa: S101 - "run" loaded AgentSettings above
    return _run(cases, settings, args)


def _context(cases: list[EvalCase], settings: RunSettings, verbose: bool, min_recall: float) -> int:
    budget = eval_budget(settings)
    lines = ["| Case | Recall | Missing | Files | Excerpts | Bytes |", "|---|---|---|---|---|---|"]
    details: list[str] = []
    below = []
    recalls = []
    for case in cases:
        snapshot, score = context_case(case, budget)
        recalls.append(score.recall)
        if score.recall < min_recall:
            below.append(case.id)
        lines.append(f"| {case.id} | {score.recall:.0%} | {', '.join(score.missing) or '–'} | {score.files} | "
                     f"{score.excerpts} | {score.bytes:,} |")
        if verbose:
            details += ["", f"{case.id}:", *(f"  {c.path}: {c.reason}{' (excerpt)' if c.excerpt else ''}"
                                              for c in snapshot.context)]
    mean = sum(recalls) / len(recalls) if recalls else 0.0
    print("\n".join([*lines, "", f"Mean recall: {mean:.0%}", *details]))
    if below:
        print(f"\nBelow --min-recall {min_recall:.0%}: {', '.join(below)}", file=sys.stderr)
    return 1 if below else 0


def _validate(cases: list[EvalCase], settings: RunSettings) -> int:
    unsound = 0
    with build_verifier(settings) as verifier:
        if verifier is None:
            print("Note: SANDBOX_URL is not set, so only the static checks ran (no build, no tests).\n")
        for case in cases:
            problems = validate_case(case, verifier)
            unsound += bool(problems)
            print(f"{'ok ' if not problems else 'BAD'} {case.id}")
            for p in problems:
                print("    - " + p.replace("\n", "\n      "))
    print(f"\n{len(cases) - unsound}/{len(cases)} case(s) sound")
    return 1 if unsound else 0


def _run(cases: list[EvalCase], settings: AgentSettings, args: argparse.Namespace) -> int:
    if not settings.sandbox_url and not args.no_sandbox:
        print("Configuration error: SANDBOX_URL is not set, so no change could be verified. Run inside the "
              "service container (see evals/README.md) or pass --no-sandbox.", file=sys.stderr)
        return 2
    if args.repeat < 1:
        print("Eval error: --repeat must be at least 1", file=sys.stderr)
        return 2
    variants = [VARIANTS[v] for v in (args.variant or list(VARIANTS))]
    budget = eval_budget(settings)
    started = datetime.now(UTC)
    records: list[RunRecord] = []
    with build_eval_agents(settings) as agents:
        for case in cases:
            for variant in variants:
                for i in range(1, args.repeat + 1):
                    run = run_case(case, variant, agents, budget, repeat=i)
                    records.append(RunRecord.from_run(run))
                    print(f"{case.id} / {variant.name} #{i}: {'resolved' if run.resolved else run.outcome}",
                          file=sys.stderr)
    meta = report_meta(coder_model=settings.openai_model, reviewer_model=settings.reviewer_model,
                       budget={"max_context_bytes": budget.max_context_bytes,
                               "max_file_bytes": budget.max_file_bytes, "max_attempts": budget.max_attempts},
                       suite=str(args.suite), started_at=started.isoformat(timespec="seconds"))
    stem = args.out / f"eval-{started.strftime('%Y%m%dT%H%M%SZ')}"
    summary = markdown_summary(meta, records)
    write_report(stem.with_suffix(".json"), meta, records)
    stem.with_suffix(".md").write_text(summary, encoding="utf-8")
    print(summary, end="")
    print(f"\nReport: {stem.with_suffix('.json')}", file=sys.stderr)
    return 0
