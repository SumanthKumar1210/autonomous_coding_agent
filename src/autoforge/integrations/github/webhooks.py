"""GitHub webhook handling: signature verification and event -> ``Trigger`` parsing.

GitHub signs every delivery with the webhook secret: header
``X-Hub-Signature-256: sha256=<HMAC-SHA256 of the raw body>``. A request
whose signature does not match is not from GitHub and must be rejected.

Events that produce a trigger (everything else is ignored):

- ``issues`` / ``labeled`` with the trigger label (e.g. ``autoforge``) -> RUN
- ``issue_comment`` / ``created`` on an issue whose first line is
  ``/autoforge approve`` or ``/autoforge reject`` -> APPROVE / REJECT

Parsing does **not** decide whether the sender is allowed to trigger
AutoForge; the worker checks write access (``TicketSource.is_authorized``)
before acting, so the webhook can answer GitHub quickly.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from autoforge.domain.models import Trigger, TriggerKind

APPROVAL_COMMANDS = {"/autoforge approve": TriggerKind.APPROVE, "/autoforge reject": TriggerKind.REJECT}
"""Issue-comment commands (first line, case-insensitive) that decide a paused run."""


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Return True if ``signature_header`` is GitHub's valid HMAC-SHA256 signature of ``body``.

    Uses a constant-time comparison so the check leaks no timing information.

    Args:
        secret: The webhook secret configured on GitHub (``GITHUB_WEBHOOK_SECRET``).
        body: The raw request body, exactly as received.
        signature_header: Value of ``X-Hub-Signature-256`` (``"sha256=<hex>"``), if present.
    """
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


def parse_trigger(
    event: str, payload: dict[str, Any], *, delivery_id: str, repo: str, trigger_label: str
) -> Trigger | None:
    """Turn a GitHub webhook delivery into a ``Trigger``, or ``None`` if AutoForge should ignore it.

    Args:
        event: The ``X-GitHub-Event`` header, e.g. ``"issues"``.
        payload: The parsed JSON body.
        delivery_id: The ``X-GitHub-Delivery`` header (unique per delivery, reused on redelivery).
        repo: The configured target repo (``"owner/name"``); events from other repos are ignored.
        trigger_label: The label that requests a run, e.g. ``"autoforge"``.

    Returns:
        The trigger, or ``None`` for events AutoForge does not act on.
    """
    if (payload.get("repository") or {}).get("full_name", "").lower() != repo.lower():
        return None
    issue = payload.get("issue") or {}
    if "number" not in issue or "pull_request" in issue:  # PR events also arrive as "issues"
        return None
    actor = (payload.get("sender") or {}).get("login")
    if not actor:
        return None
    key = str(issue["number"])
    action = payload.get("action")

    if event == "issues" and action == "labeled":
        if (payload.get("label") or {}).get("name") == trigger_label and issue.get("state") == "open":
            return Trigger(TriggerKind.RUN, key, delivery_id, actor)
        return None

    if event == "issue_comment" and action == "created":
        body = ((payload.get("comment") or {}).get("body") or "").strip()
        first_line = body.splitlines()[0].strip().lower() if body else ""
        if kind := APPROVAL_COMMANDS.get(first_line):
            return Trigger(kind, key, delivery_id, actor)
    return None
