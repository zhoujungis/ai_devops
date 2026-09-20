"""The confirm-before-execute mechanism.

The shape of this module is the guarantee:

* an agent can only ever produce an :class:`~apps.ai.models.AIRecommendation`;
* :data:`EXECUTORS` is keyed by a fixed set of codes, so a model cannot invent an
  action — it names one of the codes or its proposal cannot be executed;
* :func:`execute_recommendation` is the only path that runs an executor, and it
  refuses anything that is not still pending.

A rejection therefore touches no domain table at all. That is asserted by test rather
than left to careful reading.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.ai.models import (
    AIConfirmation,
    AIRecommendation,
    AuditLog,
    ConfirmationDecision,
    RecommendationStatus,
)
from apps.ai.services.audit import record_human_action


class ConfirmationError(RuntimeError):
    """A recommendation could not be executed or rejected."""


@dataclass(frozen=True)
class ExecutionContext:
    """Everything an executor is allowed to know."""

    project: Any
    recommendation: AIRecommendation
    #: What the user approved, which may differ from what was proposed.
    payload: dict[str, Any]
    user: Any = None
    request_id: str = ""


@dataclass(frozen=True)
class ExecutionResult:
    """What an executor created, named so the API can report it precisely."""

    summary: str
    created: tuple[dict[str, str], ...] = field(default_factory=tuple)


Executor = Callable[[ExecutionContext], ExecutionResult]

EXECUTORS: dict[str, Executor] = {}


def register_executor(code: str) -> Callable[[Executor], Executor]:
    def decorator(func: Executor) -> Executor:
        if code in EXECUTORS:
            raise ConfirmationError(f"Executor {code!r} is already registered.")
        EXECUTORS[code] = func
        return func

    return decorator


def available_executors() -> list[str]:
    return sorted(EXECUTORS)


def get_executor(code: str) -> Executor:
    if code not in EXECUTORS:
        # Registers the built-in executors on first use, rather than importing domain
        # models at module import time (which would run during app loading).
        import services.executors  # noqa: F401

    try:
        return EXECUTORS[code]
    except KeyError as exc:
        raise ConfirmationError(
            f"No executor named {code!r}. Available: {', '.join(available_executors()) or '(none)'}"
        ) from exc


def propose(
    *,
    project: Any,
    agent_code: str,
    type: str,
    title: str,
    description: str = "",
    payload: dict[str, Any] | None = None,
    job: Any = None,
    finding: Any = None,
    risk_level: str = "medium",
) -> AIRecommendation:
    """Record a proposal. This is the *most* an agent can do."""
    try:
        get_executor(type)
    except ConfirmationError as exc:
        raise ConfirmationError(
            f"{agent_code} proposed {type!r}, which is not a registered action. An "
            f"agent cannot introduce a new kind of change."
        ) from exc

    return AIRecommendation.objects.create(
        project=project,
        job=job,
        finding=finding,
        agent_code=agent_code,
        type=type,
        title=title[:500],
        description=description,
        payload=payload or {},
        risk_level=risk_level,
    )


@transaction.atomic
def execute_recommendation(
    recommendation: AIRecommendation,
    *,
    user: Any = None,
    edited_payload: dict[str, Any] | None = None,
    request_id: str = "",
) -> AIRecommendation:
    """Run the executor for a confirmed recommendation.

    Atomic: a partially applied proposal is worse than a failed one, because nobody
    can tell afterwards what made it in.
    """
    if recommendation.status != RecommendationStatus.PENDING:
        raise ConfirmationError(
            f"Recommendation {recommendation.pk} is {recommendation.status}, not pending."
        )

    executor = get_executor(recommendation.type)
    payload = edited_payload if edited_payload is not None else recommendation.payload
    context = ExecutionContext(
        project=recommendation.project,
        recommendation=recommendation,
        payload=payload,
        user=user,
        request_id=request_id,
    )

    try:
        result = executor(context)
    except Exception as exc:
        recommendation.status = RecommendationStatus.FAILED
        recommendation.save(update_fields=["status", "updated_at"])
        AIConfirmation.objects.create(
            recommendation=recommendation,
            requested_by=user,
            decision=ConfirmationDecision.CONFIRMED,
            edited_payload=payload,
            executor_code=recommendation.type,
            result={"error": f"{type(exc).__name__}: {exc}"},
            executed_at=timezone.now(),
        )
        record_human_action(
            org=recommendation.project.org,
            user=user,
            action="ai_recommendation.failed",
            target_type="ai_recommendation",
            target_id=str(recommendation.pk),
            after={"error": str(exc), "executor": recommendation.type},
            request_id=request_id,
        )
        raise

    recommendation.status = RecommendationStatus.EXECUTED
    recommendation.save(update_fields=["status", "updated_at"])

    confirmation = AIConfirmation.objects.create(
        recommendation=recommendation,
        requested_by=user,
        decision=ConfirmationDecision.CONFIRMED,
        edited_payload=payload,
        executor_code=recommendation.type,
        result={"summary": result.summary, "created": list(result.created)},
        executed_at=timezone.now(),
    )

    _audit_outcome(
        recommendation=recommendation,
        user=user,
        decision=ConfirmationDecision.CONFIRMED,
        executor_code=recommendation.type,
        after={"summary": result.summary, "created": list(result.created)},
        request_id=request_id,
        confirmation=confirmation,
    )
    return recommendation


@transaction.atomic
def reject_recommendation(
    recommendation: AIRecommendation,
    *,
    user: Any = None,
    reason: str = "",
    request_id: str = "",
) -> AIRecommendation:
    """Decline a proposal. No executor runs, so nothing is written but the decision."""
    if recommendation.status != RecommendationStatus.PENDING:
        raise ConfirmationError(
            f"Recommendation {recommendation.pk} is {recommendation.status}, not pending."
        )

    recommendation.status = RecommendationStatus.REJECTED
    recommendation.save(update_fields=["status", "updated_at"])

    confirmation = AIConfirmation.objects.create(
        recommendation=recommendation,
        requested_by=user,
        decision=ConfirmationDecision.REJECTED,
        reason=reason,
    )
    _audit_outcome(
        recommendation=recommendation,
        user=user,
        decision=ConfirmationDecision.REJECTED,
        executor_code="",
        after={"reason": reason},
        request_id=request_id,
        confirmation=confirmation,
    )
    return recommendation


def _audit_outcome(
    *,
    recommendation: AIRecommendation,
    user: Any,
    decision: str,
    executor_code: str,
    after: dict[str, Any],
    request_id: str,
    confirmation: AIConfirmation,
) -> AuditLog:
    return record_human_action(
        org=recommendation.project.org,
        user=user,
        action=f"ai_recommendation.{decision}",
        target_type="ai_recommendation",
        target_id=str(recommendation.pk),
        before={
            "type": recommendation.type,
            "agent_code": recommendation.agent_code,
            "payload": recommendation.payload,
        },
        after={**after, "executor": executor_code, "confirmation_id": str(confirmation.pk)},
        request_id=request_id,
    )
