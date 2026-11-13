"""Tests for app.webhook: the HTTP service, end to end through FastAPI's test client (worker not started)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from autoforge.app.config import Settings
from autoforge.app.webhook import create_app
from autoforge.domain.models import TriggerKind
from autoforge.integrations.sqlite.job_store import SqliteJobStore
from tests.test_webhooks import commented, labeled, sign

SECRET = "webhook-secret"


def settings(**overrides: Any) -> Settings:
    """Settings for tests, independent of any local ``.env`` file."""
    values: dict[str, Any] = {"openai_api_key": "k", "openai_model": "m", "github_token": "t",
                              "github_repo": "acme/sandbox", "github_webhook_secret": SECRET, **overrides}
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
def store() -> SqliteJobStore:
    """An in-memory job store shared with the app under test."""
    return SqliteJobStore(":memory:")


@pytest.fixture
def client(store: SqliteJobStore) -> Iterator[TestClient]:
    """A test client for the app (lifespan runs; worker disabled)."""
    with TestClient(create_app(settings(), store=store, start_worker=False)) as c:
        yield c


def post(client: TestClient, event: str, payload: dict[str, Any], *, delivery: str = "d-1",
         secret: str = SECRET) -> Any:
    """POST a signed GitHub delivery."""
    body = json.dumps(payload).encode()
    return client.post("/webhook", content=body, headers={
        "X-GitHub-Event": event, "X-GitHub-Delivery": delivery, "X-Hub-Signature-256": sign(secret, body),
        "Content-Type": "application/json"})


def test_labelled_issue_is_queued(client: TestClient, store: SqliteJobStore) -> None:
    """A signed ``labeled`` event is accepted with 202 and becomes a queued RUN job."""
    resp = post(client, "issues", labeled())

    assert resp.status_code == 202 and resp.json()["status"] == "queued"
    job = store.claim_next()
    assert job is not None and job.trigger.kind is TriggerKind.RUN and job.trigger.actor == "maintainer"


def test_invalid_signature_is_rejected(client: TestClient, store: SqliteJobStore) -> None:
    """A delivery signed with the wrong secret gets 401 and queues nothing."""
    resp = post(client, "issues", labeled(), secret="wrong")

    assert resp.status_code == 401
    assert store.claim_next() is None


def test_redelivery_and_double_label_are_deduplicated(client: TestClient, store: SqliteJobStore) -> None:
    """The same delivery twice, or a second RUN for a queued ticket, is answered 200 'duplicate'."""
    assert post(client, "issues", labeled(), delivery="d-1").status_code == 202

    assert post(client, "issues", labeled(), delivery="d-1").json()["status"] == "duplicate"
    assert post(client, "issues", labeled(), delivery="d-2").json()["status"] == "duplicate"
    assert store.claim_next() is not None and store.claim_next() is None


def test_approval_comment_is_queued(client: TestClient, store: SqliteJobStore) -> None:
    """``/autoforge approve`` becomes an APPROVE job."""
    assert post(client, "issue_comment", commented("/autoforge approve")).status_code == 202
    job = store.claim_next()
    assert job is not None and job.trigger.kind is TriggerKind.APPROVE


def test_ping_and_irrelevant_events(client: TestClient, store: SqliteJobStore) -> None:
    """``ping`` gets 'pong'; unrelated events get 200 'ignored' and queue nothing."""
    assert post(client, "ping", {"zen": "hi"}).json()["status"] == "pong"
    assert post(client, "issues", labeled("bug")).json()["status"] == "ignored"
    assert post(client, "issue_comment", commented("nice")).json()["status"] == "ignored"
    assert store.claim_next() is None


def test_malformed_requests(client: TestClient) -> None:
    """Missing delivery ID or invalid JSON (correctly signed) gets 400."""
    body = b"not json"
    headers = {"X-GitHub-Event": "issues", "X-GitHub-Delivery": "d", "X-Hub-Signature-256": sign(SECRET, body)}
    assert client.post("/webhook", content=body, headers=headers).status_code == 400
    assert post(client, "issues", labeled(), delivery="").status_code == 400


def test_healthz(client: TestClient) -> None:
    """The health check answers 200 when the worker is not used."""
    assert client.get("/healthz").json() == {"status": "ok"}


def test_service_refuses_to_start_without_secret() -> None:
    """Without GITHUB_WEBHOOK_SECRET the service will not start (unsigned webhooks are unsafe)."""
    with pytest.raises(ValueError, match="GITHUB_WEBHOOK_SECRET"):
        create_app(settings(github_webhook_secret=None), store=SqliteJobStore(":memory:"), start_worker=False)
