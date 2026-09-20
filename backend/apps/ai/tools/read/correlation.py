"""The tool that hands an agent the whole correlation chain at once.

It exists because that chain is a *deterministic query*, not something an agent
should reconstruct out of six separate tool calls and then reason about. Letting the
agent call `CorrelationService` directly would be the same data, computed once and
identically, which is exactly the property the product depends on.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from apps.ai.tools.base import Citation, Tool, ToolContext, ToolResult, ToolScopeError
from apps.codebase.models import Commit
from services.correlation import CorrelationService


class ExplainCommitArgs(BaseModel):
    sha: str = Field(description="Commit sha to explain.")


class ExplainCommitTool(Tool[ExplainCommitArgs]):
    name = "explain_commit"
    description = (
        "Return the full correlation chain for a commit: modules touched, candidate "
        "regression tests with the reason each was chosen, historical bugs in the same "
        "modules, the requirement it serves, releases containing it, and an explicit "
        "list of what could not be resolved. Start here."
    )
    args_model = ExplainCommitArgs

    def run(self, context: ToolContext, args: ExplainCommitArgs) -> ToolResult:
        commit = (
            Commit.objects.filter(repository__project=context.project, sha=args.sha)
            .select_related("repository")
            .first()
        )
        if commit is None:
            raise ToolScopeError(f"No commit {args.sha} in this project.")

        explanation = CorrelationService().explain_commit(commit)
        citations = [
            Citation(kind="commit", ref_id=str(commit.pk), label=commit.short_sha),
        ]
        citations.extend(
            Citation(
                kind="test_case",
                ref_id=str(candidate.test_case.pk),
                label=candidate.test_case.key,
            )
            for candidate in explanation.regression_candidates
        )
        citations.extend(
            Citation(kind="bug", ref_id=str(entry.bug.pk), label=entry.bug.key)
            for entry in explanation.historical_bugs
        )
        if explanation.requirement is not None:
            citations.append(
                Citation(
                    kind="requirement",
                    ref_id=str(explanation.requirement.pk),
                    label=explanation.requirement.external_key,
                )
            )

        return ToolResult(data=_serialise(explanation), citations=tuple(citations))


def _serialise(explanation: Any) -> dict[str, Any]:
    return {
        "commit": {
            "sha": explanation.commit.sha,
            "short_sha": explanation.commit.short_sha,
            "message": explanation.commit.message,
            "committed_at": explanation.commit.committed_at.isoformat(),
            "additions": explanation.commit.additions,
            "deletions": explanation.commit.deletions,
        },
        "modules": [
            {
                "path_prefix": impact.module.path_prefix,
                "name": impact.module.name,
                "weight": impact.weight,
                "churn_lines": impact.churn_lines,
                "file_count": impact.file_count,
                "is_test_change": impact.is_test_change,
            }
            for impact in explanation.modules
        ],
        "requirement": (
            {
                "external_key": explanation.requirement.external_key,
                "title": explanation.requirement.title,
                "status": explanation.requirement.status,
            }
            if explanation.requirement is not None
            else None
        ),
        "requirement_source": explanation.requirement_source,
        "regression_candidates": [
            {
                "key": candidate.test_case.key,
                "title": candidate.test_case.title,
                "priority": candidate.test_case.priority,
                "score": candidate.score,
                "reasons": list(candidate.reasons),
            }
            for candidate in explanation.regression_candidates
        ],
        "historical_bugs": [
            {
                "key": entry.bug.key,
                "title": entry.bug.title,
                "severity": entry.bug.severity,
                "status": entry.bug.status,
                "shared_modules": list(entry.shared_modules),
            }
            for entry in explanation.historical_bugs
        ],
        "releases": [
            {"version": release.version, "status": release.status}
            for release in explanation.releases
        ],
        "data_gaps": list(explanation.data_gaps),
    }
