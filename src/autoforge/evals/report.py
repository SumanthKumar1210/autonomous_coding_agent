"""Eval reports: flat JSON records, per-variant aggregates, Markdown summaries and comparisons.

A report is one JSON file::

    {"meta": {...models, prompt fingerprint, budget...}, "records": [{...one per case run...}]}

Records hold only plain values, so old reports stay readable and two reports
(e.g. before and after a prompt change) can be compared with ``compare``.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from autoforge.agents.languages import LANGUAGES
from autoforge.agents.prompts import CODER_SYSTEM_PROMPT, REVIEWER_SYSTEM_PROMPT
from autoforge.evals.grading import ContextScore
from autoforge.evals.runner import CaseRun

SCHEMA_VERSION = 1
"""Bumped when the record format changes incompatibly."""


@dataclass(frozen=True)
class RunRecord:
    """One case run, flattened to plain values for the JSON report."""

    case_id: str
    variant: str
    repeat: int
    run_id: str
    outcome: str
    resolved: bool
    error: str = ""
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    coder_model: str = ""
    reviewer_model: str = ""
    context_recall: float | None = None
    context_missing: list[str] = field(default_factory=list)
    context_bytes: int = 0
    has_change: bool = False
    policy_error: str = ""
    scope_ok: bool = False
    changed_missing: list[str] = field(default_factory=list)
    unchanged_violations: list[str] = field(default_factory=list)
    patterns_missing: list[str] = field(default_factory=list)
    hidden_passed: int = 0
    hidden_total: int = 0
    checks_skipped: str = ""
    failed_checks: list[str] = field(default_factory=list)

    @classmethod
    def from_run(cls, run: CaseRun) -> RunRecord:
        """Flatten a ``CaseRun``."""
        ctx: ContextScore | None = run.context
        change = run.change
        return cls(
            case_id=run.case_id, variant=run.variant, repeat=run.repeat, run_id=run.run_id, outcome=run.outcome,
            resolved=run.resolved, error=run.error, attempts=run.attempts, input_tokens=run.usage.input_tokens,
            output_tokens=run.usage.output_tokens, seconds=run.seconds, coder_model=run.coder_model,
            reviewer_model=run.reviewer_model,
            context_recall=ctx.recall if ctx else None, context_missing=list(ctx.missing) if ctx else [],
            context_bytes=ctx.bytes if ctx else 0,
            has_change=change is not None,
            policy_error=change.policy_error if change else "",
            scope_ok=change.scope_ok if change else False,
            changed_missing=list(change.changed_missing) if change else [],
            unchanged_violations=list(change.unchanged_violations) if change else [],
            patterns_missing=list(change.patterns_missing) if change else [],
            hidden_passed=change.hidden_passed if change else 0,
            hidden_total=change.hidden_total if change else 0,
            checks_skipped=change.checks_skipped if change else "",
            failed_checks=[f"{c.name} ({c.status}): {c.output[-500:]}" for c in change.checks if not c.passed]
            if change else [],
        )


@dataclass(frozen=True)
class Aggregate:
    """Totals of one variant over all its runs.

    Attributes:
        runs: Number of case runs.
        resolved_rate: Share of runs whose final change was resolved.
        scope_rate: Share of runs whose change touched the right files and matched the patterns.
        hidden_pass_rate: Share of hidden checks passed (over runs where they ran).
        context_recall: Mean context recall.
        attempts: Mean coder attempts.
        input_tokens: Mean input tokens per run.
        output_tokens: Mean output tokens per run.
        seconds: Mean agent time per run.
        outcomes: Count of each outcome.
        false_rejects: Runs the reviewer rejected although the change was resolved.
        false_accepts: Runs the reviewer approved (completed) although the change was not resolved.
        unverified: Runs whose checks could not run (no sandbox, sandbox error); they count as unresolved.

    ``false_rejects`` and ``false_accepts`` judge the reviewer, so they only count runs in which a
    reviewer gave a verdict, and are ``None`` for a variant without one (e.g. ``single``, ``no-review``).
    """

    runs: int
    resolved_rate: float
    scope_rate: float
    hidden_pass_rate: float
    context_recall: float
    attempts: float
    input_tokens: float
    output_tokens: float
    seconds: float
    outcomes: dict[str, int]
    false_rejects: int | None
    false_accepts: int | None
    unverified: int


def report_meta(*, coder_model: str, reviewer_model: str, budget: dict[str, int], suite: str,
                started_at: str, aborted: str = "") -> dict[str, Any]:
    """The report header: what was measured, with which models and prompts, and why it stopped early (if it did)."""
    return {"schema": SCHEMA_VERSION, "started_at": started_at, "suite": suite, "coder_model": coder_model,
            "reviewer_model": reviewer_model, "prompt_fingerprint": prompt_fingerprint(), "budget": budget,
            "aborted": aborted}


def prompt_fingerprint() -> str:
    """Short hash of every system prompt and language rule, so reports show when the prompts changed."""
    digest = hashlib.sha256()
    for text in (CODER_SYSTEM_PROMPT, REVIEWER_SYSTEM_PROMPT,
                 *(rule for lang in LANGUAGES for rule in (lang.coder_rules, lang.reviewer_rules))):
        digest.update(text.encode() + b"\0")
    return digest.hexdigest()[:12]


def write_report(path: Path, meta: dict[str, Any], records: list[RunRecord]) -> None:
    """Write the report as JSON (creating the folder)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "records": [asdict(r) for r in records]}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_report(path: Path) -> tuple[dict[str, Any], list[RunRecord]]:
    """Read a report written by ``write_report``.

    Raises:
        ValueError: If the file is not a report of a known schema version.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("meta", {}).get("schema") != SCHEMA_VERSION:
        raise ValueError(f"{path} is not an eval report of schema version {SCHEMA_VERSION}")
    known = set(RunRecord.__dataclass_fields__)
    return data["meta"], [RunRecord(**{k: v for k, v in r.items() if k in known}) for r in data["records"]]


def aggregate(records: list[RunRecord]) -> dict[str, Aggregate]:
    """Aggregate the records per variant (in first-seen order)."""
    by_variant: dict[str, list[RunRecord]] = {}
    for r in records:
        by_variant.setdefault(r.variant, []).append(r)
    return {name: _aggregate(rows) for name, rows in by_variant.items()}


def _aggregate(rows: list[RunRecord]) -> Aggregate:
    n = len(rows)
    hidden_total = sum(r.hidden_total for r in rows)
    recalls = [r.context_recall for r in rows if r.context_recall is not None]
    reviewed = [r for r in rows if r.reviewer_model]  # a reviewer gave a verdict ("" without one)
    return Aggregate(
        runs=n,
        resolved_rate=sum(r.resolved for r in rows) / n,
        scope_rate=sum(r.scope_ok for r in rows) / n,
        hidden_pass_rate=sum(r.hidden_passed for r in rows) / hidden_total if hidden_total else 0.0,
        context_recall=sum(recalls) / len(recalls) if recalls else 0.0,
        attempts=sum(r.attempts for r in rows) / n,
        input_tokens=sum(r.input_tokens for r in rows) / n,
        output_tokens=sum(r.output_tokens for r in rows) / n,
        seconds=sum(r.seconds for r in rows) / n,
        outcomes=dict(Counter(r.outcome for r in rows)),
        false_rejects=sum(1 for r in reviewed if r.outcome == "review_rejected" and r.resolved)
        if reviewed else None,
        false_accepts=sum(1 for r in reviewed if r.outcome == "completed" and r.has_change and not r.resolved
                          and not r.checks_skipped) if reviewed else None,
        unverified=sum(1 for r in rows if r.has_change and r.checks_skipped and not r.policy_error),
    )


def markdown_summary(meta: dict[str, Any], records: list[RunRecord]) -> str:
    """Markdown report: one row per variant, reviewer lift, then one row per case."""
    aggs = aggregate(records)
    lines = [
        "# AutoForge eval",
        "",
        f"Coder `{meta.get('coder_model', '?')}` · reviewer `{meta.get('reviewer_model', '?')}` · "
        f"prompts `{meta.get('prompt_fingerprint', '?')}` · {meta.get('started_at', '')}",
        "",
        *(["> [!WARNING]", f"> Incomplete run: {meta['aborted']}", ""] if meta.get("aborted") else []),
        "| Variant | Runs | Resolved | Scope ok | Hidden checks | Context recall | Attempts | Tokens in/out | "
        "Time | False accepts | False rejects | Unverified |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, a in aggs.items():
        lines.append(f"| {name} | {a.runs} | {_pct(a.resolved_rate)} | {_pct(a.scope_rate)} | "
                     f"{_pct(a.hidden_pass_rate)} | {_pct(a.context_recall)} | {a.attempts:.1f} | "
                     f"{a.input_tokens:,.0f} / {a.output_tokens:,.0f} | {a.seconds:.0f} s | "
                     f"{_count(a.false_accepts)} | {_count(a.false_rejects)} | {a.unverified} |")
    if "full" in aggs and "no-review" in aggs:
        lift = aggs["full"].resolved_rate - aggs["no-review"].resolved_rate
        cost = aggs["full"].input_tokens + aggs["full"].output_tokens
        base = aggs["no-review"].input_tokens + aggs["no-review"].output_tokens
        lines += ["", f"**Reviewer lift** (full − no-review): {lift * 100:+.0f} pp resolved, "
                      f"{cost - base:+,.0f} tokens per run."]
    if any(a.unverified for a in aggs.values()):
        lines += ["", "> Unverified runs had no sandbox for their checks, so they count as unresolved. "
                      "Run inside the service container to verify (see evals/README.md)."]

    variants = list(aggs)
    lines += ["", "| Case | " + " | ".join(variants) + " |", "|---|" + "---|" * len(variants)]
    for case_id in sorted({r.case_id for r in records}):
        cells = []
        for v in variants:
            rows = [r for r in records if r.case_id == case_id and r.variant == v]
            cells.append(f"{sum(r.resolved for r in rows)}/{len(rows)} " + _case_note(rows) if rows else "–")
        lines.append(f"| {case_id} | " + " | ".join(cells) + " |")
    lines += ["", "Cells: resolved runs / runs, then the outcome of the last run if it was not resolved."]
    return "\n".join(lines) + "\n"


def compare(old: tuple[dict[str, Any], list[RunRecord]], new: tuple[dict[str, Any], list[RunRecord]]) -> str:
    """Markdown comparison of two reports, variant by variant (new − old)."""
    (old_meta, old_rows), (new_meta, new_rows) = old, new
    old_aggs, new_aggs = aggregate(old_rows), aggregate(new_rows)
    lines = [
        "# AutoForge eval comparison",
        "",
        "| | Old | New |",
        "|---|---|---|",
    ]
    for key in ("started_at", "coder_model", "reviewer_model", "prompt_fingerprint", "aborted"):
        lines.append(f"| {key} | {old_meta.get(key, '?')} | {new_meta.get(key, '?')} |")
    lines += ["", "| Variant | Runs | Resolved | Scope ok | Hidden checks | Context recall | Tokens/run |",
              "|---|---|---|---|---|---|---|"]
    for name in [*new_aggs, *(n for n in old_aggs if n not in new_aggs)]:
        o, n = old_aggs.get(name), new_aggs.get(name)
        if o is None or n is None:
            lines.append(f"| {name} | only in {'new' if o is None else 'old'} report | | | | | |")
            continue
        lines.append(
            f"| {name} | {o.runs} → {n.runs} | {_delta(o.resolved_rate, n.resolved_rate)} | "
            f"{_delta(o.scope_rate, n.scope_rate)} | {_delta(o.hidden_pass_rate, n.hidden_pass_rate)} | "
            f"{_delta(o.context_recall, n.context_recall)} | "
            f"{o.input_tokens + o.output_tokens:,.0f} → {n.input_tokens + n.output_tokens:,.0f} |")
    changed = []
    for case_id in sorted({r.case_id for r in old_rows} & {r.case_id for r in new_rows}):
        for v in sorted({r.variant for r in old_rows} & {r.variant for r in new_rows}):
            before = _rate([r for r in old_rows if r.case_id == case_id and r.variant == v])
            after = _rate([r for r in new_rows if r.case_id == case_id and r.variant == v])
            if before is not None and after is not None and before != after:
                changed.append(f"- {case_id} / {v}: {_pct(before)} → {_pct(after)} resolved")
    lines += ["", "Cases that changed:" if changed else "No case changed its resolved rate.", *changed]
    return "\n".join(lines) + "\n"


def _rate(rows: list[RunRecord]) -> float | None:
    return sum(r.resolved for r in rows) / len(rows) if rows else None


def _case_note(rows: list[RunRecord]) -> str:
    last = rows[-1]
    if last.resolved:
        return ""
    if last.policy_error:
        return "(policy)"
    if last.checks_skipped:
        return f"({last.outcome}, unverified)"
    if last.failed_checks:
        return f"({last.outcome}, {len(last.failed_checks)} check(s) failed)"
    return f"({last.outcome})"


def _count(value: int | None) -> str:
    return "–" if value is None else str(value)


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _delta(old: float, new: float) -> str:
    return f"{_pct(old)} → {_pct(new)} ({(new - old) * 100:+.0f} pp)"
