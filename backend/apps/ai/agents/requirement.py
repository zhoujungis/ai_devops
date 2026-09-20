"""The Requirement agent: what a requirement implies, and what it is missing."""

from __future__ import annotations

from typing import Any

from apps.ai.agents.analysis import AnalysisAgent
from apps.ai.agents.base import register_agent
from apps.ai.models import AIAnalysisJob
from apps.ai.schemas.requirement import RequirementOutput
from apps.ai.tools.base import ToolContext
from apps.bugs.models import BugStatus
from apps.codebase.models import CommitModuleImpact
from apps.requirements.models import Requirement
from apps.testing.models import TestCase


@register_agent
class RequirementAgent(AnalysisAgent):
    code = "requirement_analysis"
    prompt_id = "requirement_analysis"
    capability = "chat"
    description = "Reviews a requirement for risks, gaps and missing scenarios."
    schema = RequirementOutput
    finding_category = "requirement_analysis"

    def gather(self, job: AIAnalysisJob, context: ToolContext) -> tuple[dict[str, Any] | None, str]:
        requirement = self._requirement(job, context)
        if requirement is None:
            return None, ""

        module_ids = list(requirement.module_links.values_list("module_id", flat=True))

        facts: dict[str, Any] = {
            "requirement": {
                "external_key": requirement.external_key,
                "title": requirement.title,
                "description": requirement.description,
                "status": requirement.status,
                "priority": requirement.priority,
                "acceptance_criteria": requirement.acceptance_criteria,
                "items": [
                    {"type": item.type, "text": item.text} for item in requirement.items.all()
                ],
            },
            "implementing_modules": list(
                requirement.module_links.select_related("module").values_list(
                    "module__path_prefix", flat=True
                )
            ),
            "existing_test_cases": [
                {
                    "key": case.key,
                    "title": case.title,
                    "expected": case.expected[:200],
                    "priority": case.priority,
                }
                for case in self._existing_cases(requirement, context)
            ],
            "defects_in_these_modules": [
                {
                    "key": link.bug.key,
                    "title": link.bug.title,
                    "severity": link.bug.severity,
                    "status": link.bug.status,
                }
                for link in requirement.bug_links.select_related("bug")
                if link.bug.status not in {BugStatus.CLOSED, BugStatus.RESOLVED}
            ],
            "recent_changes_in_these_modules": [
                {
                    "sha": impact.commit.sha[:12],
                    "message": (impact.commit.message or "").splitlines()[0][:120],
                }
                for impact in CommitModuleImpact.objects.filter(module_id__in=module_ids)
                .select_related("commit")
                .order_by("-commit__committed_at")[:15]
            ],
        }
        return facts, requirement.external_key

    @staticmethod
    def _requirement(job: AIAnalysisJob, context: ToolContext) -> Requirement | None:
        queryset = Requirement.objects.filter(project=context.project).prefetch_related("items")
        if not job.target_id:
            return None
        return (
            queryset.filter(pk=job.target_id).first()
            or queryset.filter(external_key__iexact=job.target_id).first()
        )

    @staticmethod
    def _existing_cases(requirement: Requirement, context: ToolContext) -> list[TestCase]:
        queryset = TestCase.objects.filter(project=context.project).exclude(status="deprecated")
        linked = queryset.filter(requirement_links__requirement=requirement).distinct()
        if linked.exists():
            return list(linked.order_by("key")[:50])
        # No explicit link: fall back to cases covering the same modules, which is the
        # weakest evidence the prompt is allowed to treat as "already covered".
        return list(
            queryset.filter(
                module_links__module_id__in=requirement.module_links.values_list(
                    "module_id", flat=True
                )
            )
            .distinct()
            .order_by("key")[:50]
        )
