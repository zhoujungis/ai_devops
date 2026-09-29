"""Async analysis jobs.

An HTTP request only ever creates a job and returns. The work happens here, on a
worker, because an LLM call has no business holding a request open — and because a
job that outlives its request is exactly what makes the analysis traceable.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.ai.agents.base import AgentError, agent_for
from apps.ai.models import AIAnalysisJob, JobStatus
from apps.ai.providers.base import AIProviderError
from apps.ai.providers.registry import resolve
from apps.ai.tools.base import ToolContext
from apps.ai.tools.registry import load_default_tools

logger = logging.getLogger(__name__)

#: Long enough for a rate-limit window or a provider hiccup, short enough that a
#: genuinely broken provider does not retry forever.
TRANSIENT_RETRY_SECONDS = 120

#: A job still QUEUED after this long was almost certainly never enqueued — the broker
#: was unreachable when the request was served, so no worker knows about it.
STALE_QUEUED_SECONDS = 30 * 60
#: A job still RUNNING after this long has outlived CELERY_TASK_TIME_LIMIT and is not
#: coming back.
STALE_RUNNING_SECONDS = 2 * 60 * 60


def create_job(
    *,
    project: Any,
    agent_code: str,
    target_type: str = "",
    target_id: str = "",
    params: dict[str, Any] | None = None,
    requested_by: Any = None,
    idempotency_key: str = "",
) -> tuple[AIAnalysisJob, bool]:
    """Queue a job, returning the existing one when ``idempotency_key`` repeats.

    ``idempotency_key`` exists because a client that times out and retries must not
    be charged for the same analysis twice.
    """
    if idempotency_key:
        existing = AIAnalysisJob.objects.filter(
            project=project, idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            return existing, False

    try:
        # The unique constraint on (project, idempotency_key) is the real guard: a
        # check-then-create alone lets two concurrent retries both pass the check,
        # and the loser would surface as a 500 instead of the job it already has.
        # The nested atomic keeps the failed INSERT from poisoning an outer
        # transaction, so the re-read below can run.
        with transaction.atomic():
            job = AIAnalysisJob.objects.create(
                project=project,
                agent_code=agent_code,
                target_type=target_type,
                target_id=target_id,
                params=params or {},
                requested_by=requested_by,
                idempotency_key=idempotency_key,
            )
    except IntegrityError:
        existing = AIAnalysisJob.objects.filter(
            project=project, idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            return existing, False
        raise
    return job, True


@shared_task(bind=True, max_retries=2)
def run_analysis_job(self: Any, job_id: str) -> dict[str, Any]:
    job = (
        AIAnalysisJob.objects.select_related("project", "project__org", "requested_by")
        .filter(pk=job_id)
        .first()
    )
    if job is None:
        logger.warning("Analysis requested for unknown job %s", job_id)
        return {"status": "missing", "job_id": job_id}
    if job.is_finished:
        return {"status": "already_finished", "job_id": job_id}

    job.status = JobStatus.RUNNING
    job.started_at = timezone.now()
    job.error = ""
    job.save(update_fields=["status", "started_at", "error", "updated_at"])

    try:
        agent = agent_for(job.agent_code)
        load_default_tools()
        resolved = resolve(job.project.org, agent.capability)
        try:
            payload = agent.run(
                job,
                context=ToolContext(
                    project=job.project,
                    user=job.requested_by,
                    job_id=str(job.pk),
                ),
                provider=resolved.provider,
                model=resolved.model,
            )
        finally:
            resolved.close()
    except (AgentError, AIProviderError) as exc:
        # Configuration problems: retrying cannot help, a human must act.
        _fail(job, exc)
        logger.exception("Analysis job %s failed permanently", job_id)
        return {"status": "failed", "job_id": job_id, "error": str(exc)}
    except Exception as exc:
        _fail(job, exc)
        raise self.retry(exc=exc, countdown=TRANSIENT_RETRY_SECONDS) from exc

    job.status = JobStatus.SUCCEEDED
    job.result = payload
    job.progress = 1.0
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "result", "progress", "finished_at", "updated_at"])
    return {"status": "succeeded", "job_id": job_id}


def _fail(job: AIAnalysisJob, exc: Exception) -> None:
    job.status = JobStatus.FAILED
    job.error = f"{type(exc).__name__}: {exc}"[:2000]
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error", "finished_at", "updated_at"])


@shared_task
def fail_stale_jobs() -> dict[str, int]:
    """Reconcile jobs that will never finish.

    The job row is committed before it is enqueued, so a broker outage at that moment
    leaves a job no worker has ever seen. Nothing else would move it, and a client
    polling ``GET /ai/jobs/{id}`` would poll it forever — this sweeper (run by Celery
    beat) is what closes that gap. It is deliberately conservative: the thresholds sit
    well past the task time limit so a slow-but-alive job is never killed.
    """
    now = timezone.now()
    queued = AIAnalysisJob.objects.filter(
        status=JobStatus.QUEUED,
        created_at__lt=now - timedelta(seconds=STALE_QUEUED_SECONDS),
    ).update(
        status=JobStatus.FAILED,
        error="Never picked up by a worker.",
        finished_at=now,
        updated_at=now,
    )
    running = AIAnalysisJob.objects.filter(
        status=JobStatus.RUNNING,
        started_at__lt=now - timedelta(seconds=STALE_RUNNING_SECONDS),
    ).update(
        status=JobStatus.FAILED,
        error="Exceeded the task time limit.",
        finished_at=now,
        updated_at=now,
    )
    if queued or running:
        logger.warning("Swept stale analysis jobs: %s queued, %s running", queued, running)
    return {"queued": queued, "running": running}
