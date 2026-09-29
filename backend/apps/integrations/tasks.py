"""Celery tasks for git synchronisation and webhook processing.

Task names are left at Celery's default (``apps.integrations.tasks.<function>``) so
the queue routing in settings matches on module path and never drifts.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.integrations.git.base import (
    GitAuthenticationError,
    GitNotFoundError,
    GitProviderError,
    GitRateLimitError,
)
from apps.integrations.git.factory import provider_for
from apps.integrations.models import Repository, SyncStatus, WebhookEvent
from apps.integrations.sync import sync_repository
from apps.integrations.webhooks import process_event

logger = logging.getLogger(__name__)

#: The API budget resets on GitHub's schedule, not ours, so back off generously.
RATE_LIMIT_RETRY_SECONDS = 300
TRANSIENT_RETRY_SECONDS = 60


@shared_task(bind=True, max_retries=5)
def sync_repository_task(self: Any, repository_id: str, limit: int | None = None) -> dict[str, Any]:
    repository = (
        Repository.objects.select_related("connection", "project").filter(pk=repository_id).first()
    )
    if repository is None:
        logger.warning("Sync requested for unknown repository %s", repository_id)
        return {"status": "missing", "repository_id": repository_id}

    provider = provider_for(repository.connection)
    try:
        result = sync_repository(repository, provider=provider, limit=limit)
    except GitRateLimitError as exc:
        raise self.retry(exc=exc, countdown=RATE_LIMIT_RETRY_SECONDS) from exc
    except (GitAuthenticationError, GitNotFoundError):
        # A configuration problem: retrying cannot fix it, a human must.
        logger.exception("Sync configuration error for %s", repository.full_name)
        raise
    except GitProviderError as exc:
        raise self.retry(exc=exc, countdown=TRANSIENT_RETRY_SECONDS) from exc
    finally:
        provider.close()

    logger.info(
        "Synced %s: %s new commits in %s pages",
        repository.full_name,
        result.commits_created,
        result.pages_fetched,
    )
    return {"status": "succeeded", **result.as_dict()}


@shared_task
def sync_stale_repositories() -> dict[str, int]:
    """Queue a sync for every repository whose data has gone stale.

    Webhooks are the primary trigger; this is the reconciliation that catches what they
    missed (a delivery dropped, a hook never configured, a worker restart). The window
    is ``GIT_SYNC_INTERVAL_MINUTES``, which is also how often beat runs this, so a
    repository a webhook already refreshed is left alone — and one already syncing is
    skipped rather than queued twice.
    """
    cutoff = timezone.now() - timedelta(minutes=settings.GIT_SYNC_INTERVAL_MINUTES)
    stale = list(
        Repository.objects.filter(
            models.Q(last_synced_at__lt=cutoff) | models.Q(last_synced_at__isnull=True)
        )
        .exclude(sync_status=SyncStatus.RUNNING)
        .values_list("pk", flat=True)
    )

    for repository_id in stale:
        sync_repository_task.delay(str(repository_id))

    if stale:
        logger.info("Queued %s stale repositories for sync", len(stale))
    return {"queued": len(stale)}


@shared_task
def process_webhook_event_task(event_id: str) -> dict[str, Any]:
    event = WebhookEvent.objects.select_related("connection").filter(pk=event_id).first()
    if event is None:
        return {"status": "missing", "event_id": event_id}
    if event.processed_at is not None:
        return {"status": "already_processed", "event_id": event_id}

    try:
        outcome = process_event(event)
    except Exception as exc:
        event.error = f"{type(exc).__name__}: {exc}"[:2000]
        event.save(update_fields=["error", "updated_at"])
        raise

    event.processed_at = timezone.now()
    event.save(update_fields=["processed_at", "updated_at"])
    return {"status": "processed", "outcome": outcome}
