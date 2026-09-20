"""Read tools over defects."""

from __future__ import annotations

from pydantic import BaseModel, Field

from apps.ai.tools.base import Citation, Tool, ToolContext, ToolResult
from apps.bugs.models import Bug, BugStatus

DEFAULT_LIMIT = 20


class SearchBugArgs(BaseModel):
    query: str | None = Field(
        default=None, description="Text in the title, description or error type."
    )
    module_path_prefix: str | None = Field(
        default=None, description="Only bugs linked to this module."
    )
    severity: str | None = Field(default=None, description="One of s1, s2, s3, s4.")
    include_closed: bool = Field(default=False, description="Include resolved and closed bugs.")
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=100)


class SearchBugTool(Tool[SearchBugArgs]):
    name = "search_bug"
    description = (
        "Find defects by text, affected module or severity. Use it to check whether a "
        "change touches an area with a history of problems."
    )
    args_model = SearchBugArgs

    def run(self, context: ToolContext, args: SearchBugArgs) -> ToolResult:
        queryset = Bug.objects.filter(project=context.project)
        if not args.include_closed:
            queryset = queryset.exclude(status__in=[BugStatus.CLOSED, BugStatus.RESOLVED])
        if args.query:
            queryset = queryset.filter(title__icontains=args.query)
        if args.severity:
            queryset = queryset.filter(severity=args.severity)
        if args.module_path_prefix:
            queryset = queryset.filter(
                module_links__module__path_prefix=args.module_path_prefix
            ).distinct()

        bugs = list(queryset.order_by("-created_at")[: args.limit])
        return ToolResult(
            data=[
                {
                    "key": bug.key,
                    "title": bug.title,
                    "severity": bug.severity,
                    "status": bug.status,
                    "error_type": bug.error_type,
                    "occurrence_count": bug.occurrence_count,
                    "description": bug.description[:300],
                }
                for bug in bugs
            ],
            citations=tuple(
                Citation(kind="bug", ref_id=str(bug.pk), label=bug.key) for bug in bugs
            ),
        )


class GetBugArgs(BaseModel):
    key: str = Field(description="Bug key, e.g. BUG-1023.")


class GetBugTool(Tool[GetBugArgs]):
    name = "get_bug"
    description = "Fetch one bug by key, including its stack trace and linked modules."
    args_model = GetBugArgs

    def run(self, context: ToolContext, args: GetBugArgs) -> ToolResult:
        bug = (
            Bug.objects.filter(project=context.project, key__iexact=args.key)
            .prefetch_related("module_links__module", "occurrences")
            .first()
        )
        if bug is None:
            # Not a scope violation: another tenant's bug key is simply absent from
            # this project's namespace.
            return ToolResult(data=None)

        return ToolResult(
            data={
                "key": bug.key,
                "title": bug.title,
                "description": bug.description[:1000],
                "severity": bug.severity,
                "status": bug.status,
                "error_type": bug.error_type,
                "stack_trace": bug.stack_trace[:2000],
                "environment": bug.environment,
                "first_seen_at": bug.first_seen_at.isoformat() if bug.first_seen_at else None,
                "last_seen_at": bug.last_seen_at.isoformat() if bug.last_seen_at else None,
                "occurrence_count": bug.occurrence_count,
                "modules": [link.module.path_prefix for link in bug.module_links.all()],
            },
            citations=(Citation(kind="bug", ref_id=str(bug.pk), label=bug.key),),
        )
