"""The actions an approved AI proposal can carry out.

Every executor takes an :class:`~apps.ai.services.confirmation.ExecutionContext` and
returns what it created. They are the *only* code that turns an AI suggestion into
rows, and they run only after a human confirmed.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from apps.ai.services.confirmation import (
    ConfirmationError,
    ExecutionContext,
    ExecutionResult,
    register_executor,
)
from apps.bugs.models import Bug, BugStatus, Severity
from apps.codebase.models import Module
from apps.core.enums import Priority
from apps.core.models import LinkSource
from apps.testing.models import (
    TestCase,
    TestCaseModuleLink,
    TestCaseOrigin,
    TestCaseStatus,
)

PRIORITIES = {choice.value for choice in Priority}
SEVERITIES = {choice.value for choice in Severity}


def _coerce(value: Any, allowed: set[str], fallback: str) -> str:
    """Keep only values the model is allowed to choose, defaulting otherwise."""
    candidate = str(value or "")
    return candidate if candidate in allowed else fallback


def _key_prefix(project: Any, fallback: str) -> str:
    """The project's configured key prefix, or a default.

    ``Project.key_prefix`` exists so a team's keys look like their own (``PAY-18``);
    without it the historical ``TC`` / ``BUG`` default keeps existing projects stable.
    """
    candidate = str(getattr(project, "key_prefix", "") or "").strip().upper()
    return candidate or fallback


def _next_test_case_key(project: Any) -> str:
    """The first unused ``<prefix>-nnn``, in one query.

    Counting rows and probing candidate keys cost a query per existing row; this reads
    the project's keys once. It is still read-then-write, so two concurrent approvals
    on one project can pick the same key — the unique constraint then rejects the
    loser rather than letting it corrupt anything.
    """
    prefix = _key_prefix(project, "TC")
    existing = set(TestCase.objects.filter(project=project).values_list("key", flat=True))
    index = 1
    while f"{prefix}-{index:03d}" in existing:
        index += 1
    return f"{prefix}-{index:03d}"


def _next_bug_key(project: Any) -> str:
    """The first unused ``<prefix>-n``. See :func:`_next_test_case_key` for the caveat."""
    prefix = _key_prefix(project, "BUG")
    existing = set(Bug.objects.filter(project=project).values_list("key", flat=True))
    index = 1
    while f"{prefix}-{index}" in existing:
        index += 1
    return f"{prefix}-{index}"


@register_executor("create_test_cases")
def create_test_cases(context: ExecutionContext) -> ExecutionResult:
    """Create the approved test cases, and link them to the modules they cover.

    Anything the payload omits is defaulted rather than rejected: a user approving
    eleven cases should not have the eleventh fail because a tag list was missing.
    """
    items = context.payload.get("test_cases") or []
    if not items:
        raise ConfirmationError("The approved payload contains no test cases.")

    created: list[dict[str, str]] = []
    with transaction.atomic():
        for index, item in enumerate(items, start=1):
            if not item.get("title"):
                raise ConfirmationError(f"Test case #{index} has no title.")

            test_case = TestCase.objects.create(
                project=context.project,
                key=(item.get("key") or "").strip() or _next_test_case_key(context.project),
                title=item["title"][:500],
                precondition=item.get("precondition") or "",
                expected=item.get("expected") or "",
                priority=_coerce(item.get("priority"), PRIORITIES, Priority.P2.value),
                tags=item.get("tags") or [],
                automation=item.get("automation") or "manual",
                automation_suggestion=item.get("automation_suggestion") or {},
                origin=TestCaseOrigin.AI_GENERATED,
                status=TestCaseStatus.ACTIVE,
                created_by=context.user,
            )

            for prefix in item.get("module_path_prefixes") or []:
                module = Module.objects.filter(project=context.project, path_prefix=prefix).first()
                if module is not None:
                    TestCaseModuleLink.objects.get_or_create(
                        test_case=test_case,
                        module=module,
                        defaults={"source": LinkSource.AI, "confidence": 0.6},
                    )

            created.append({"type": "test_case", "id": str(test_case.pk), "key": test_case.key})

    return ExecutionResult(summary=f"Created {len(created)} test cases", created=tuple(created))


@register_executor("create_bug")
def create_bug(context: ExecutionContext) -> ExecutionResult:
    payload = context.payload
    if not payload.get("title"):
        raise ConfirmationError("The approved payload contains no bug title.")

    bug = Bug.objects.create(
        project=context.project,
        key=(payload.get("key") or "").strip() or _next_bug_key(context.project),
        title=payload["title"][:500],
        description=payload.get("description") or "",
        severity=_coerce(payload.get("severity"), SEVERITIES, Severity.S3.value),
        priority=_coerce(payload.get("priority"), PRIORITIES, Priority.P2.value),
        status=BugStatus.OPEN,
        error_type=(payload.get("error_type") or "")[:200],
        stack_trace=payload.get("stack_trace") or "",
        environment=(payload.get("environment") or "")[:100],
        reporter=context.user,
    )
    return ExecutionResult(
        summary=f"Created bug {bug.key}",
        created=({"type": "bug", "id": str(bug.pk), "key": bug.key},),
    )
