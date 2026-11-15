"""Scripted stand-in for the verification sandbox: FakeVerifier (no containers, nothing executed)."""

from __future__ import annotations

from autoforge.domain.models import CheckResult, CheckSpec, CodeChange, VerificationReport


def passing(*names: str) -> VerificationReport:
    """A report where every named check passed."""
    return VerificationReport(checks=[CheckResult(n, f"run {n}", "passed", 0, "ok", 1.5) for n in names])


def failing(name: str = "cmake: .", output: str = "src/a.c:3: error: 'x' undeclared", *,
            preexisting: bool = False) -> VerificationReport:
    """A report with one failing check (caused by the change unless ``preexisting``)."""
    return VerificationReport(checks=[CheckResult(name, f"run {name}", "failed", 2, output, 3.0, preexisting)])


class FakeVerifier:
    """Returns scripted reports in order (the last one repeats), and records every call."""

    def __init__(self, *reports: VerificationReport, error: Exception | None = None) -> None:
        """Configure the reports to return (default: one passing check), or an ``error`` to raise."""
        self.reports = list(reports) or [passing("cmake: .")]
        self.error = error
        self.calls: list[tuple[bytes, CodeChange, list[CheckSpec]]] = []

    def verify(self, archive: bytes, change: CodeChange, checks: list[CheckSpec]) -> VerificationReport:
        """Record the call and return the next scripted report (or raise)."""
        self.calls.append((archive, change, checks))
        if self.error:
            raise self.error
        return self.reports[min(len(self.calls), len(self.reports)) - 1]
