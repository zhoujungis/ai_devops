"""Who did what, and what changed as a result.

Kept separate from the AI tables on purpose: an audit trail that only covered AI
actions would be half an audit trail, and the interesting questions ("who created
this?", "what did the AI change?") need one place to look.
"""

from __future__ import annotations

from typing import Any

from apps.ai.models import ActorType, AuditLog


def record_audit(
    *,
    org: Any,
    actor_type: str,
    action: str,
    actor_id: str = "",
    target_type: str = "",
    target_id: str = "",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    request_id: str = "",
    ip: str | None = None,
) -> AuditLog:
    return AuditLog.objects.create(
        org=org,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        before=before or {},
        after=after or {},
        request_id=request_id,
        ip=ip,
    )


def record_human_action(
    *,
    org: Any,
    user: Any,
    action: str,
    target_type: str = "",
    target_id: str = "",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    request_id: str = "",
) -> AuditLog:
    return record_audit(
        org=org,
        actor_type=ActorType.USER,
        actor_id=str(getattr(user, "pk", "") or ""),
        action=action,
        target_type=target_type,
        target_id=target_id,
        before=before,
        after=after,
        request_id=request_id,
    )
