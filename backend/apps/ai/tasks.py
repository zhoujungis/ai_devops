"""Async analysis jobs.

An HTTP request only ever creates a job and returns. The work happens here, on a
worker, because an LLM call has no business holding a request open — and because a
job that outlives its request is exactly what makes the analysis traceable.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
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

    job = AIAnalysisJob.objects.create(
        project=project,
        agent_code=agent_code,
        target_type=target_type,
        target_id=target_id,
        params=params or {},
        requested_by=requested_by,
        idempotency_key=idempotency_key,
    )
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
