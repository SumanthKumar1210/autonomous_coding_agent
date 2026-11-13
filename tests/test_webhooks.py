"""Tests for integrations.github.webhooks: HMAC verification and event -> Trigger parsing."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import pytest

from autoforge.domain.models import Trigger, TriggerKind
from autoforge.integrations.github.webhooks import parse_trigger, verify_signature

REPO = "acme/sandbox"


def sign(secret: str, body: bytes) -> str:
    """Compute the ``X-Hub-Signature-256`` header GitHub would send."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def labeled(label: str = "autoforge", **issue: Any) -> dict[str, Any]:
    """An ``issues``/``labeled`` payload for issue #7 on the sandbox repo."""
    return {"action": "labeled", "label": {"name": label}, "repository": {"full_name": REPO},
            "sender": {"login": "maintainer"}, "issue": {"number": 7, "state": "open", **issue}}


def commented(body: str, **issue: Any) -> dict[str, Any]:
    """An ``issue_comment``/``created`` payload on issue #7."""
    return {"action": "created", "comment": {"body": body}, "repository": {"full_name": REPO},
            "sender": {"login": "maintainer"}, "issue": {"number": 7, "state": "open", **issue}}


def parse(event: str, payload: dict[str, Any]) -> Trigger | None:
    """Parse with the sandbox repo and the default trigger label."""
    return parse_trigger(event, payload, delivery_id="d-1", repo=REPO, trigger_label="autoforge")


def test_valid_signature_is_accepted() -> None:
    """A signature computed with the shared secret over the exact body verifies."""
    body = b'{"zen": "hi"}'
    assert verify_signature("s3cret", body, sign("s3cret", body))


@pytest.mark.parametrize("header", [None, "", "sha1=abc", "sha256=" + "0" * 64])
def test_missing_or_wrong_signature_is_rejected(header: str | None) -> None:
    """No header, a SHA-1 header, or a wrong digest all fail."""
    assert not verify_signature("s3cret", b"{}", header)


def test_signature_with_other_secret_or_tampered_body_is_rejected() -> None:
    """Changing the secret or a single byte of the body breaks the signature."""
    body = b'{"a": 1}'
    assert not verify_signature("other", body, sign("s3cret", body))
    assert not verify_signature("s3cret", b'{"a": 2}', sign("s3cret", body))


def test_trigger_label_requests_a_run() -> None:
    """Adding the trigger label produces a RUN trigger with the sender and delivery ID."""
    assert parse("issues", labeled()) == Trigger(TriggerKind.RUN, "7", "d-1", "maintainer")


@pytest.mark.parametrize("event, payload", [
    ("issues", labeled("bug")),                                    # another label
    ("issues", labeled("autoforge:pr-opened")),                    # AutoForge's own status label
    ("issues", labeled(state="closed")),                           # closed issue
    ("issues", labeled(pull_request={"url": "x"})),                # a pull request
    ("issues", {**labeled(), "repository": {"full_name": "evil/repo"}}),  # other repository
    ("issues", {**labeled(), "action": "unlabeled"}),
    ("push", labeled()),
])
def test_other_events_are_ignored(event: str, payload: dict[str, Any]) -> None:
    """Only the trigger label on an open issue of the configured repo starts a run."""
    assert parse(event, payload) is None


@pytest.mark.parametrize("body, kind", [
    ("/autoforge approve", TriggerKind.APPROVE),
    ("  /AutoForge Approve  \nLooks good to me.", TriggerKind.APPROVE),
    ("/autoforge reject", TriggerKind.REJECT),
])
def test_approval_commands_in_comments(body: str, kind: TriggerKind) -> None:
    """``/autoforge approve|reject`` on the first line of a comment decides a paused run."""
    trigger = parse("issue_comment", commented(body))
    assert trigger is not None and trigger.kind is kind and trigger.actor == "maintainer"


@pytest.mark.parametrize("payload", [
    commented("Please /autoforge approve"),           # command not on the first line start
    commented("thanks!"),
    commented("/autoforge approve", pull_request={"url": "x"}),  # comment on a PR
])
def test_other_comments_are_ignored(payload: dict[str, Any]) -> None:
    """Ordinary comments, and commands on pull requests, are ignored."""
    assert parse("issue_comment", payload) is None
