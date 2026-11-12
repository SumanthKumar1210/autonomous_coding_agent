"""Command-line entry point: ``autoforge run <issue-number>``.

Usage::

    autoforge run 12          # or: python -m autoforge run 12

Output:
    - stdout: one JSON line with ``run_id``, ``outcome``, ``pr_url``, ``error``
    - stderr: structured JSON logs of the run

Exit codes:
    0 = PR opened (or one was already open), 1 = run failed, 2 = configuration error
"""

from __future__ import annotations

import argparse
import json
import sys

from pydantic import ValidationError

from autoforge.app.config import Settings
from autoforge.app.wiring import build_dependencies, workflow_config
from autoforge.observability import configure_logging
from autoforge.workflow.pipeline import RunOutcome, run_ticket


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, load settings, run the ticket and print the result.

    Args:
        argv: Command-line arguments without the program name. ``None`` means
            use ``sys.argv`` (normal use); tests can pass a list.

    Returns:
        Process exit code: 0 success, 1 run failed, 2 configuration error.
    """
    parser = argparse.ArgumentParser(prog="autoforge", description="AutoForge coding agent")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Implement a GitHub issue and open a pull request")
    run.add_argument("issue", help="GitHub issue number, e.g. 12 or #12")
    args = parser.parse_args(argv)

    try:
        settings = Settings()  # type: ignore[call-arg]  # populated from env / .env
    except ValidationError as exc:
        # Report only the names of missing/invalid settings, never their values.
        missing = ", ".join(".".join(str(p) for p in e["loc"]).upper() for e in exc.errors())
        print(f"Configuration error (check .env): {missing}", file=sys.stderr)
        return 2

    configure_logging(settings.log_level)
    with build_dependencies(settings) as deps:
        result = run_ticket(args.issue, deps, workflow_config(settings))

    print(json.dumps({"run_id": result.run_id, "outcome": result.outcome.value,
                      "pr_url": result.pr_url, "error": result.error}))
    return 1 if result.outcome is RunOutcome.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
