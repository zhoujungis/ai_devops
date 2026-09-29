"""Read tools over requirements."""

from __future__ import annotations

from pydantic import BaseModel, Field

from apps.ai.tools.base import Citation, Tool, ToolContext, ToolResult
from apps.requirements.models import Requirement

DEFAULT_LIMIT = 20


class SearchRequirementArgs(BaseModel):
    query: str | None = Field(default=None, description="Text in the key or title.")
    module_path_prefix: str | None = Field(
        default=None, description="Only requirements linked to this module."
    )
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=100)


class SearchRequirementTool(Tool[SearchRequirementArgs]):
    name = "search_requirement"
    description = (
        "Find requirements by key, title or implementing module. Use it to work out "
        "which requirement a change serves when the commit does not say."
    )
    args_model = SearchRequirementArgs

    def run(self, context: ToolContext, args: SearchRequirementArgs) -> ToolResult:
        queryset = Requirement.objects.filter(project=context.project)
        if args.query:
            queryset = queryset.filter(title__icontains=args.query) | queryset.filter(
                external_key__icontains=args.query
            )
        if args.module_path_prefix:
            queryset = queryset.filter(module_links__module__path_prefix=args.module_path_prefix)

        # `distinct` covers both filters: the OR on title/key can match a row twice, and
        # the module join can too. Without it the same requirement is cited twice.
        requirements = list(queryset.distinct().order_by("external_key")[: args.limit])
        return ToolResult(
            data=[
                {
                    "external_key": requirement.external_key,
                    "title": requirement.title,
                    "status": requirement.status,
                    "priority": requirement.priority,
                    "description": requirement.description[:500],
                }
                for requirement in requirements
            ],
            citations=tuple(
                Citation(
                    kind="requirement",
                    ref_id=str(requirement.pk),
                    label=requirement.external_key,
                )
                for requirement in requirements
            ),
        )


class GetRequirementArgs(BaseModel):
    external_key: str = Field(description="Requirement key, e.g. PAY-18.")


class GetRequirementTool(Tool[GetRequirementArgs]):
    name = "get_requirement"
    description = "Fetch one requirement with its extracted items and acceptance criteria."
    args_model = GetRequirementArgs

    def run(self, context: ToolContext, args: GetRequirementArgs) -> ToolResult:
        requirement = (
            Requirement.objects.filter(
                project=context.project, external_key__iexact=args.external_key
            )
            .prefetch_related("items")
            .first()
        )
        if requirement is None:
            return ToolResult(data=None)

        return ToolResult(
            data={
                "external_key": requirement.external_key,
                "title": requirement.title,
                "description": requirement.description,
                "status": requirement.status,
                "priority": requirement.priority,
                "acceptance_criteria": requirement.acceptance_criteria,
                "items": [
                    {"seq": item.seq, "type": item.type, "text": item.text}
                    for item in requirement.items.all()
                ],
            },
            citations=(
                Citation(
                    kind="requirement",
                    ref_id=str(requirement.pk),
                    label=requirement.external_key,
                ),
            ),
        )
