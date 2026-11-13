"""Command-line entry point.

Usage::

    autoforge serve [--host 0.0.0.0] [--port 8080]   # webhook service + background worker
    autoforge run 12                    # implement issue #12 now (or: python -m autoforge run 12)
    autoforge resume <run_id>           # approve a run paused for approval -> opens the PR
    autoforge resume <run_id> --reject  # reject it -> no PR

``run`` goes through the same job store and ``process_job`` as the service, so
a run started here can later be approved with an ``/autoforge approve``
comment once the service is up.

Output (``run`` / ``resume``):
    - stdout: one JSON line with ``run_id``, ``outcome``, ``pr_url``, ``error``
    - stderr: structured JSON logs of the run

Exit codes:
    0 = success (PR opened, already open, awaiting approval, rejected, skipped);
    1 = run failed; 2 = configuration error
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

from pydantic import ValidationError

from autoforge.app.config import Settings
from autoforge.app.wiring import build_dependencies, build_job_store, workflow_config
from autoforge.domain.models import Trigger, TriggerKind
from autoforge.observability import configure_logging
from autoforge.workflow.pipeline import RunOutcome, resume_run
from autoforge.workflow.worker import JobResult, process_job


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, load settings, and run the chosen command.

    Args:
        argv: Command-line arguments without the program name. ``None`` means
            use ``sys.argv`` (normal use); tests can pass a list.

    Returns:
        Process exit code: 0 success, 1 run failed, 2 configuration error.
    """
    parser = argparse.ArgumentParser(prog="autoforge", description="AutoForge coding agent")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Run the GitHub webhook service and background worker")
    serve.add_argument("--host", default="127.0.0.1", help="Interface to bind (0.0.0.0 in a container)")
    serve.add_argument("--port", type=int, default=8080, help="Port to listen on")
    run = sub.add_parser("run", help="Implement a GitHub issue and open a pull request")
    run.add_argument("issue", help="GitHub issue number, e.g. 12 or #12")
    resume = sub.add_parser("resume", help="Approve (default) or reject a run waiting for approval")
    resume.add_argument("run_id", help="Run ID printed when the run paused")
    resume.add_argument("--reject", action="store_true", help="Reject the change instead of opening the PR")
    args = parser.parse_args(argv)

    try:
        settings = Settings()
    except ValidationError as exc:
        # Report only the names of missing/invalid settings, never their values.
        missing = ", ".join(".".join(str(p) for p in e["loc"]).upper() for e in exc.errors())
        print(f"Configuration error (check .env): {missing}", file=sys.stderr)
        return 2

    configure_logging(settings.log_level)
    if args.command == "serve":
        return _serve(settings, args.host, args.port)

    config = workflow_config(settings)
    with build_dependencies(settings) as deps, build_job_store(settings) as store:
        if args.command == "run":
            # Local operator: trusted (actor None), processed now (claim=True) so the service's worker never takes it.
            trigger = Trigger(TriggerKind.RUN, args.issue.lstrip("#"), f"cli-{uuid.uuid4().hex}")
            job = store.add(trigger, claim=True)
            if job is None:  # cannot happen: the delivery ID is a fresh UUID
                raise RuntimeError("could not record the CLI job")
            result = process_job(job, deps, config, store)
        else:
            r = resume_run(args.run_id, not args.reject, deps, config)
            result = JobResult(r.outcome.value, r.run_id, r.pr_url, r.error)

    print(json.dumps({"run_id": result.run_id, "outcome": result.outcome,
                      "pr_url": result.pr_url, "error": result.error}))
    return 1 if result.outcome == RunOutcome.FAILED.value else 0


def _serve(settings: Settings, host: str, port: int) -> int:
    """Run the webhook service with uvicorn until interrupted."""
    import uvicorn  # imported here so `run` / `resume` start fast

    from autoforge.app.webhook import create_app

    try:
        app = create_app(settings)
    except ValueError as exc:
        print(f"Configuration error (check .env): {exc}", file=sys.stderr)
        return 2
    uvicorn.run(app, host=host, port=port, log_config=None, access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
