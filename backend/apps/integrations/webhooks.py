"""Signature verification and handling of inbound git webhooks."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from apps.codebase.ingest import ingest_branch, ingest_commit
from apps.codebase.models import Commit
from apps.integrations.git.base import GitProviderError, RemoteBranch
from apps.integrations.git.factory import provider_for
from apps.integrations.models import Repository, WebhookEvent
from services.linking import link_unlinked_commits

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Hub-Signature-256"
DELIVERY_HEADER = "X-GitHub-Delivery"
EVENT_HEADER = "X-GitHub-Event"

#: A single push should never be able to queue unbounded work.
MAX_COMMITS_PER_DELIVERY = 50


def compute_signature(secret: str, body: bytes) -> str:
    """The ``sha256=...`` value GitHub sends for a given body."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(*, secret: str, body: bytes, signature_header: str) -> bool:
    """Constant-time comparison of the delivery signature against the shared secret."""
    if not secret or not signature_header:
        return False
    return hmac.compare_digest(compute_signature(secret, body), signature_header)


def process_event(event: WebhookEvent) -> str:
    """Handle a stored delivery. Returns a short, loggable outcome."""
    if event.event_type == "ping":
        return "pong"
    if event.event_type == "push":
        return _handle_push(event)
    return f"ignored ({event.event_type})"


def _handle_push(event: WebhookEvent) -> str:
    payload: dict[str, Any] = event.payload
    full_name = (payload.get("repository") or {}).get("full_name", "")
    repository = Repository.objects.filter(connection=event.connection, full_name=full_name).first()
    if repository is None:
        return "no tracked repository"

    shas = [item.get("id") for item in payload.get("commits", []) if item.get("id")]
    if len(shas) > MAX_COMMITS_PER_DELIVERY:
        shas = shas[-MAX_COMMITS_PER_DELIVERY:]

    created = 0
    provider = provider_for(event.connection)
    try:
        for sha in shas:
            if Commit.objects.filter(repository=repository, sha=sha).exists():
                continue
            try:
                ingest_commit(repository, provider.get_commit(full_name, sha))
            except GitProviderError as exc:
                # A deleted or force-pushed commit is normal, not an incident.
                logger.warning("Could not ingest %s from %s: %s", sha, full_name, exc)
                continue
            created += 1
    finally:
        provider.close()

    _update_pushed_branch(repository, payload)
    linked = link_unlinked_commits(repository)
    return f"ingested {created}/{len(shas)} commits, linked {linked} requirements"


def _update_pushed_branch(repository: Repository, payload: dict[str, Any]) -> None:
    ref = str(payload.get("ref", ""))
    after = payload.get("after")
    if not ref.startswith("refs/heads/") or not after:
        return
    ingest_branch(
        repository,
        RemoteBranch(name=ref.removeprefix("refs/heads/"), sha=str(after)),
    )
