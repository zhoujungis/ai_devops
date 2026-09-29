"""The Bug Investigation agent: build a chain, then narrow the search.

The output has no "root cause" field. Candidate causes carry their own confidence,
reasoning and verification steps, because "we think it is X, here is how to check"
is useful and "the root cause is X" is a guess wearing a suit.
"""

from __future__ import annotations

from typing import Any, ClassVar

from apps.ai.agents.analysis import AnalysisAgent
from apps.ai.agents.base import matches_uuid, register_agent
from apps.ai.models import AIAnalysisJob
from apps.ai.schemas.bug_investigation import BugInvestigationOutput
from apps.ai.schemas.common import AgentOutput
from apps.ai.tools.base import ToolContext
from apps.bugs.models import Bug
from apps.codebase.models import Commit
from apps.testing.models import TestCase


@register_agent
class BugInvestigationAgent(AnalysisAgent):
    code = "bug_investigation"
    prompt_id = "bug_investigation"
    capability = "chat"
    description = "Builds an investigation chain for a defect and ranks candidate causes."
    # Annotated as the base type so the RCA agent can override it with another schema;
    # without this mypy narrows it to this exact class and rejects the subclass.
    schema: ClassVar[type[AgentOutput]] = BugInvestigationOutput
    finding_category = "bug_investigation"

    def gather(self, job: AIAnalysisJob, context: ToolContext) -> tuple[dict[str, Any] | None, str]:
        bug = self._bug(job, context)
        if bug is None:
            return None, ""

        module_ids = list(bug.module_links.values_list("module_id", flat=True))
        commits = self._commits_around(module_ids, bug)

        facts: dict[str, Any] = {
            "bug": {
                "key": bug.key,
                "title": bug.title,
                "description": bug.description,
                "severity": bug.severity,
                "status": bug.status,
                "error_type": bug.error_type,
                "environment": bug.environment,
                "stack_trace": bug.stack_trace[:3000],
                "first_seen_at": bug.first_seen_at.isoformat() if bug.first_seen_at else None,
                "last_seen_at": bug.last_seen_at.isoformat() if bug.last_seen_at else None,
                "occurrence_count": bug.occurrence_count,
            },
            "affected_modules": list(
                bug.module_links.select_related("module").values_list(
                    "module__path_prefix", flat=True
                )
            ),
            "recent_commits_in_these_modules": [
                {
                    "sha": commit.sha[:12],
                    "message": (commit.message or "").splitlines()[0][:160],
                    "committed_at": commit.committed_at.isoformat(),
                    "author": commit.author_name,
                }
                for commit in commits
            ],
            "similar_defects_in_these_modules": [
                {
                    "key": other.key,
                    "title": other.title,
                    "severity": other.severity,
                    "status": other.status,
                    "error_type": other.error_type,
                    "resolved_or_closed": other.status in {"resolved", "closed"},
                }
                for other in Bug.objects.filter(project=context.project)
                .filter(module_links__module_id__in=module_ids)
                .exclude(pk=bug.pk)
                .distinct()[:15]
            ],
            "test_cases_covering_these_modules": [
                {"key": case.key, "title": case.title, "expected": case.expected[:200]}
                for case in TestCase.objects.filter(project=context.project)
                .filter(module_links__module_id__in=module_ids)
                .exclude(status="deprecated")
                .distinct()[:30]
            ],
        }
        return facts, bug.key

    @staticmethod
    def _bug(job: AIAnalysisJob, context: ToolContext) -> Bug | None:
        """Resolve the target to a bug, by pk when it is one and by key otherwise."""
        queryset = Bug.objects.filter(project=context.project)
        if not job.target_id:
            return None
        by_pk = queryset.filter(pk=job.target_id).first() if matches_uuid(job.target_id) else None
        return by_pk or queryset.filter(key__iexact=job.target_id).first()

    @staticmethod
    def _commits_around(module_ids: list[Any], bug: Bug) -> list[Commit]:
        """Commits touching the affected modules, newest first."""
        queryset = Commit.objects.filter(module_impacts__module_id__in=module_ids).distinct()
        if bug.first_seen_at is not None:
            # Changes *before* the symptom appeared are the interesting ones.
            queryset = queryset.filter(committed_at__lte=bug.first_seen_at)
        return list(queryset.select_related("repository").order_by("-committed_at")[:20])
