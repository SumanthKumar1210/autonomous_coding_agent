"""The webhook service (FastAPI): GitHub events in, queued jobs out.

Endpoints:

- ``POST /webhook`` - receives GitHub deliveries. Steps: check the body size,
  verify the HMAC signature (``401`` if invalid), answer ``ping``, parse the
  event into a ``Trigger`` (or ignore it), drop duplicates (redelivered
  deliveries, a second RUN for a ticket already queued), store the job and
  wake the worker. Replies ``202`` within milliseconds; the run itself
  happens in the background ``Worker``.
- ``GET /healthz`` - liveness for Docker / the platform: ``200`` while the
  worker thread is alive, ``503`` otherwise.

Start it with ``autoforge serve`` (uvicorn). ``create_app`` builds the app;
its lifespan opens the job store and workflow dependencies once and runs
the worker thread for the life of the process.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from autoforge.app.config import Settings
from autoforge.app.wiring import build_dependencies, build_job_store, workflow_config
from autoforge.domain.models import TriggerKind
from autoforge.integrations.github.webhooks import parse_trigger, verify_signature
from autoforge.ports import JobStore
from autoforge.workflow.worker import Worker

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 5 * 1024 * 1024
"""Largest webhook body accepted (issue events are a few KB; GitHub caps payloads at 25 MB)."""


def create_app(settings: Settings, *, store: JobStore | None = None, start_worker: bool = True) -> FastAPI:
    """Build the webhook service.

    Args:
        settings: Loaded configuration. ``GITHUB_WEBHOOK_SECRET`` is required.
        store: Job store to use. ``None`` (production) opens ``JOBS_DB`` at startup.
        start_worker: ``True`` (production) builds the GitHub/OpenAI dependencies
            and runs the background worker. Tests pass ``False`` to check
            request handling only.

    Returns:
        The FastAPI application.

    Raises:
        ValueError: If ``GITHUB_WEBHOOK_SECRET`` is not set: an unsigned
            webhook would let anyone trigger runs.
    """
    if settings.github_webhook_secret is None or not settings.github_webhook_secret.get_secret_value():
        raise ValueError("GITHUB_WEBHOOK_SECRET must be set to run the webhook service")
    secret = settings.github_webhook_secret.get_secret_value()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Open the job store and dependencies, run the worker, and clean up on shutdown."""
        with ExitStack() as stack:
            app.state.store = store if store is not None else stack.enter_context(build_job_store(settings))
            app.state.worker = None
            if start_worker:
                deps = stack.enter_context(build_dependencies(settings))
                worker = Worker(app.state.store, deps, workflow_config(settings),
                                poll_seconds=settings.worker_poll_seconds)
                worker.start()
                stack.callback(worker.stop, 60)
                app.state.worker = worker
            log.info("webhook service started", extra={"repo": settings.github_repo, "worker": start_worker})
            yield
            log.info("webhook service stopping")

    app = FastAPI(title="AutoForge", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz(request: Request) -> JSONResponse:
        """Liveness: 200 while the worker thread is alive (or not used), 503 if it died."""
        worker: Worker | None = request.app.state.worker
        alive = worker is None or worker.is_alive()
        return JSONResponse({"status": "ok" if alive else "worker stopped"}, status_code=200 if alive else 503)

    @app.post("/webhook")
    async def webhook(request: Request) -> JSONResponse:
        """Verify, parse, dedupe and enqueue one GitHub delivery."""
        if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
            return _reply(413, "payload too large")
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            return _reply(413, "payload too large")
        if not verify_signature(secret, body, request.headers.get("x-hub-signature-256")):
            log.warning("rejected webhook with invalid signature")
            return _reply(401, "invalid signature")

        event = request.headers.get("x-github-event", "")
        delivery = request.headers.get("x-github-delivery", "")
        if event == "ping":
            return _reply(200, "pong")
        if not delivery:
            return _reply(400, "missing X-GitHub-Delivery")
        try:
            payload: Any = json.loads(body)
        except ValueError:
            return _reply(400, "invalid JSON")
        if not isinstance(payload, dict):
            return _reply(400, "invalid JSON")

        trigger = parse_trigger(event, payload, delivery_id=delivery, repo=settings.github_repo,
                                trigger_label=settings.trigger_label)
        if trigger is None:
            return _reply(200, "ignored")

        job_store: JobStore = request.app.state.store
        if trigger.kind is TriggerKind.RUN and await run_in_threadpool(
                job_store.has_pending, trigger.ticket_key, TriggerKind.RUN.value):
            log.info("run already queued for ticket; ignoring", extra={"ticket": trigger.ticket_key})
            return _reply(200, "duplicate")
        job = await run_in_threadpool(job_store.add, trigger)
        if job is None:
            log.info("redelivered webhook ignored", extra={"delivery": delivery})
            return _reply(200, "duplicate")

        log.info("job queued", extra={"job": job.id, "kind": trigger.kind.value, "ticket": trigger.ticket_key,
                                      "actor": trigger.actor, "delivery": delivery})
        worker: Worker | None = request.app.state.worker
        if worker is not None:
            worker.wake()
        return JSONResponse({"status": "queued", "job": job.id}, status_code=202)

    return app


def _reply(status_code: int, status: str) -> JSONResponse:
    """A small JSON reply ``{"status": ...}`` (GitHub shows it under Recent Deliveries)."""
    return JSONResponse({"status": status}, status_code=status_code)
