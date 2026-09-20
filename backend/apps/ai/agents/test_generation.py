"""The Test Generation agent.

The only agent that produces a state-changing proposal, and it does so *only* as an
:class:`~apps.ai.models.AIRecommendation`. Nothing here writes a test case: the rows
appear when a person confirms, through the executor registry.
"""

from __future__ import annotations

from typing import Any

from apps.ai.agents.analysis import AnalysisAgent
from apps.ai.agents.base import register_agent
from apps.ai.models import AIAnalysisJob, AIFinding
from apps.ai.schemas.test_generation import TestGenerationOutput
from apps.ai.services.confirmation import propose
from apps.ai.tools.base import ToolContext
from apps.codebase.models import Module
from apps.requirements.models import Requirement
from apps.testing.models import TestCase


@register_agent
class TestGenerationAgent(AnalysisAgent):
    code = "test_generation"
    prompt_id = "test_generation"
    capability = "chat"
    description = "Proposes test cases for a requirement, module or defect."
    schema = TestGenerationOutput
    finding_category = "test_generation"

    def gather(self, job: AIAnalysisJob, context: ToolContext) -> tuple[dict[str, Any] | None, str]:
        """The target may be a requirement, a module, or free text in ``params``."""
        requirement = self._requirement(job, context)
        module = self._module(job, context)
        subject = job.params.get("subject", "") if job.params else ""

        if requirement is None and module is None and not subject:
            return None, ""

        facts: dict[str, Any] = {
            "subject": subject,
            "requirement": (
                {
                    "external_key": requirement.external_key,
                    "title": requirement.title,
                    "description": requirement.description,
                    "acceptance_criteria": requirement.acceptance_criteria,
                    "items": [
                        {"type": item.type, "text": item.text} for item in requirement.items.all()
                    ],
                }
                if requirement is not None
                else None
            ),
            "module": (
                {"path_prefix": module.path_prefix, "name": module.name}
                if module is not None
                else None
            ),
            "available_module_path_prefixes": list(
                Module.objects.filter(project=context.project)
                .order_by("path_prefix")
                .values_list("path_prefix", flat=True)[:200]
            ),
            "existing_test_cases": [
                {"key": case.key, "title": case.title, "expected": case.expected[:200]}
                for case in self._existing_cases(context, requirement, module)
            ],
            "known_defects": [
                {"key": link.bug.key, "title": link.bug.title, "severity": link.bug.severity}
                for link in (module.bug_links.select_related("bug") if module is not None else [])
            ],
        }

        headline = (
            requirement.external_key
            if requirement is not None
            else module.path_prefix if module is not None else subject[:80]
        )
        return facts, headline

    def propose(
        self,
        *,
        output: Any,
        job: AIAnalysisJob,
        context: ToolContext,
        finding: AIFinding,
    ) -> list[dict[str, str]]:
        """Offer the generated cases — as a proposal, never as rows."""
        assert isinstance(output, TestGenerationOutput)
        if not output.test_cases:
            return []

        recommendation = propose(
            project=context.project,
            agent_code=self.code,
            type="create_test_cases",
            title=f"Create {len(output.test_cases)} test cases",
            description=(f"{output.summary}\n\n" f"Coverage: {_coverage_summary(output)}"),
            payload={
                "test_cases": [case.model_dump(mode="json") for case in output.test_cases],
                "coverage_matrix": [row.model_dump(mode="json") for row in output.coverage_matrix],
            },
            job=job,
            finding=finding,
            risk_level="medium",
        )
        return [{"id": str(recommendation.pk), "type": recommendation.type}]

    # ------------------------------------------------------------------
    @staticmethod
    def _requirement(job: AIAnalysisJob, context: ToolContext) -> Requirement | None:
        if not job.target_id or job.target_type not in {"requirement", ""}:
            return None
        queryset = Requirement.objects.filter(project=context.project).prefetch_related("items")
        return (
            queryset.filter(pk=job.target_id).first()
            or queryset.filter(external_key__iexact=job.target_id).first()
        )

    @staticmethod
    def _module(job: AIAnalysisJob, context: ToolContext) -> Module | None:
        if job.target_type != "module" or not job.target_id:
            return None
        queryset = Module.objects.filter(project=context.project)
        return (
            queryset.filter(pk=job.target_id).first()
            or queryset.filter(path_prefix=job.target_id).first()
        )

    @staticmethod
    def _existing_cases(
        context: ToolContext, requirement: Requirement | None, module: Module | None
    ) -> list[TestCase]:
        queryset = TestCase.objects.filter(project=context.project).exclude(status="deprecated")
        if requirement is not None:
            queryset = queryset.filter(requirement_links__requirement=requirement).distinct()
        elif module is not None:
            queryset = queryset.filter(module_links__module=module).distinct()
        return list(queryset.order_by("key")[:50])


def _coverage_summary(output: TestGenerationOutput) -> str:
    covered = [row.category for row in output.coverage_matrix if row.covered]
    gaps = [row.category for row in output.coverage_matrix if not row.covered]
    parts = []
    if covered:
        parts.append(f"covered: {', '.join(covered)}")
    if gaps:
        parts.append(f"not covered: {', '.join(gaps)}")
    return "; ".join(parts) or "no coverage matrix supplied"
