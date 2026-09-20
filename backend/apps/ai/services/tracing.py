"""Recording what an agent actually did.

Every model interaction becomes one :class:`~apps.ai.models.AIAnalysisRun`, and every
tool call one :class:`~apps.ai.models.AIToolCall`. This is what turns "the AI said the
risk is high" into something a reviewer can check: which prompt version, which model,
which rows it read, what it cost.
"""

from __future__ import annotations

import hashlib

from django.conf import settings

from apps.ai.models import (
    AIAnalysisJob,
    AIAnalysisRun,
    AIToolCall,
    RunStatus,
    ToolCallStatus,
)
from apps.ai.providers.base import ChatResult, TokenUsage
from apps.ai.services.cost import compute_cost


def next_run_sequence(job: AIAnalysisJob) -> int:
    """The next ordinal for a model interaction inside a job."""
    previous = job.runs.order_by("-sequence").values_list("sequence", flat=True).first() or 0
    return int(previous) + 1


def next_tool_call_sequence(run: AIAnalysisRun) -> int:
    """The next ordinal for a tool call inside one interaction."""
    previous = run.tool_calls.order_by("-sequence").values_list("sequence", flat=True).first() or 0
    return int(previous) + 1


def _bound(text: str) -> tuple[str, bool]:
    limit: int = settings.AI_TRACE_TEXT_MAX_BYTES
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def record_run(
    *,
    job: AIAnalysisJob,
    provider_type: str,
    model: str,
    capability: str,
    input_text: str,
    prompt_id: str = "",
    prompt_version: int = 0,
    result: ChatResult | None = None,
    error: str = "",
) -> AIAnalysisRun:
    """Persist one interaction, whether it succeeded or failed."""
    stored_input, truncated = _bound(input_text)
    usage = result.usage if result is not None else TokenUsage()

    return AIAnalysisRun.objects.create(
        job=job,
        sequence=next_run_sequence(job),
        provider_type=provider_type,
        model=result.model if result is not None else model,
        capability=capability,
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        input_digest=hashlib.sha256(input_text.encode("utf-8")).hexdigest(),
        input_text=stored_input,
        input_truncated=truncated,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=usage.cached_tokens,
        latency_ms=result.latency_ms if result is not None else 0,
        cost_usd=(
            compute_cost(provider_type=provider_type, model=model, usage=usage)
            if result is not None
            else None
        ),
        status=RunStatus.SUCCEEDED if error == "" else RunStatus.FAILED,
        error=error,
        output_text=(result.content if result is not None else "")[
            : settings.AI_TRACE_TEXT_MAX_BYTES
        ],
    )


def record_tool_call(
    *,
    run: AIAnalysisRun,
    tool_name: str,
    arguments: dict[str, object],
    result_summary: object = None,
    status: ToolCallStatus = ToolCallStatus.OK,
    duration_ms: int = 0,
    scope_denied: bool = False,
    error: str = "",
) -> AIToolCall:
    return AIToolCall.objects.create(
        run=run,
        sequence=next_tool_call_sequence(run),
        tool_name=tool_name,
        arguments=arguments,
        result_summary={"value": result_summary} if result_summary is not None else {},
        status=status,
        duration_ms=duration_ms,
        scope_denied=scope_denied,
        error=error,
    )
