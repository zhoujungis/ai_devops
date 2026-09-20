"""The Code Impact agent.

The division of labour is the whole point of this module:

* :class:`~services.correlation.CorrelationService` determines **what changed and
  what it relates to** — deterministic, reproducible SQL.
* :class:`~apps.risk.engine.RiskEngine` determines **how risky it is** — weighted
  measurable signals.
* The model determines **what it means and what to check** — the part that genuinely
  needs judgement.

The model is handed the first two as facts and asked to explain them. It is never
asked to produce a score, and the score recorded on the finding comes from the
engine, so the number a reader sees and the number the engine computed cannot drift.
"""

from __future__ import annotations

import json
from typing import Any

from apps.ai.agents.base import AgentError, BaseAgent, register_agent
from apps.ai.models import AIAnalysisJob, FindingSeverity
from apps.ai.prompts.registry import load_prompt
from apps.ai.providers.base import AIProvider, ChatMessage
from apps.ai.schemas.code_impact import CodeImpactOutput
from apps.ai.services.findings import record_finding, resolve_evidence
from apps.ai.services.tracing import record_run
from apps.ai.tools.base import ToolContext
from apps.codebase.models import Commit
from apps.risk.engine import RiskEngine
from services.correlation import CommitExplanation, CorrelationService

#: The engine's level -> a finding severity. Deliberately a total mapping so a
#: finding can never be stored without a severity.
_LEVEL_TO_SEVERITY: dict[str, str] = {
    "low": FindingSeverity.LOW,
    "medium": FindingSeverity.MEDIUM,
    "high": FindingSeverity.HIGH,
    "critical": FindingSeverity.CRITICAL,
}


@register_agent
class CodeImpactAgent(BaseAgent):
    code = "code_impact"
    prompt_id = "code_impact"
    capability = "chat"
    description = "Explains what a commit affects, why, and which tests to re-run."

    def run(
        self,
        job: AIAnalysisJob,
        *,
        context: ToolContext,
        provider: AIProvider,
        model: str,
    ) -> dict[str, Any]:
        commit = self._target_commit(job, context)
        explanation = CorrelationService().explain_commit(commit)
        assessment = RiskEngine().assess_commit(commit)

        template = load_prompt(self.prompt_id)
        messages = [
            ChatMessage(role="system", content=template.system),
            ChatMessage(role="user", content=_render_facts(explanation, assessment)),
        ]

        output, result = provider.structured_output(messages, schema=CodeImpactOutput, model=model)
        assert isinstance(output, CodeImpactOutput)  # narrowed for the type checker

        record_run(
            job=job,
            provider_type=provider.provider_type,
            model=model,
            capability=self.capability,
            input_text="\n\n".join(message.content for message in messages),
            prompt_id=template.prompt_id,
            prompt_version=template.version,
            result=result,
        )

        kept_evidence, dropped = resolve_evidence(context.project, list(output.evidence))

        finding = record_finding(
            project=context.project,
            job=job,
            agent_code=self.code,
            title=f"{commit.short_sha}: {_headline(output, explanation)}",
            summary=output.summary,
            payload={
                "output": output.model_dump(mode="json"),
                # The engine's numbers, not the model's. This is the single source of
                # truth the UI reads.
                "risk_score": assessment.score,
                "risk_level": assessment.level,
                "risk_breakdown": assessment.as_breakdown(),
                "modules": [impact.module.path_prefix for impact in explanation.modules],
                "dropped_evidence": dropped,
            },
            confidence=output.confidence,
            evidence=kept_evidence,
            severity=_LEVEL_TO_SEVERITY.get(assessment.level, FindingSeverity.MEDIUM),
            category="code_impact",
        )

        return {
            "commit": commit.sha,
            "finding_id": str(finding.pk),
            "risk_score": assessment.score,
            "risk_level": assessment.level,
            "risk_breakdown": assessment.as_breakdown(),
            "regression_candidates": [
                {
                    "key": candidate.test_case.key,
                    "title": candidate.test_case.title,
                    "score": candidate.score,
                    "reasons": list(candidate.reasons),
                }
                for candidate in explanation.regression_candidates
            ],
            "analysis": output.model_dump(mode="json"),
            "dropped_evidence": dropped,
            "data_gaps": list(explanation.data_gaps),
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _target_commit(job: AIAnalysisJob, context: ToolContext) -> Commit:
        """Resolve the commit from the job's target, scoped to the job's project."""
        queryset = Commit.objects.filter(repository__project=context.project)
        commit = (
            queryset.filter(pk=job.target_id).first() if job.target_id else None
        ) or queryset.filter(sha=job.target_id).first()

        if commit is None:
            # An AgentError, not a bare ValueError: the job runner treats agent errors
            # as permanent. A target that is not in this project will not be there on
            # the next attempt either, and retrying would just burn budget.
            raise AgentError(
                f"Job {job.pk} targets commit {job.target_id!r}, which is not in "
                f"project {context.project.slug!r}."
            )
        return commit


def _headline(output: CodeImpactOutput, explanation: CommitExplanation) -> str:
    if output.affected_features:
        return output.affected_features[0].feature[:120]
    if explanation.modules:
        return explanation.modules[0].module.path_prefix
    return "code impact analysis"


def _render_facts(explanation: CommitExplanation, assessment: Any) -> str:
    """The deterministic facts, with the engine's score stated as already decided."""
    facts = {
        "commit": {
            "sha": explanation.commit.sha,
            "message": (
                explanation.commit.message.splitlines()[0] if explanation.commit.message else ""
            ),
            "author": explanation.commit.author_name,
            "additions": explanation.commit.additions,
            "deletions": explanation.commit.deletions,
            "files_changed": explanation.commit.files_changed,
        },
        "modules_touched": [
            {
                "path_prefix": impact.module.path_prefix,
                "weight": impact.weight,
                "churn_lines": impact.churn_lines,
                "is_test_change": impact.is_test_change,
            }
            for impact in explanation.modules
        ],
        "requirement": (
            {
                "external_key": explanation.requirement.external_key,
                "title": explanation.requirement.title,
                "how_we_know": explanation.requirement_source,
            }
            if explanation.requirement is not None
            else None
        ),
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
                "shared_modules": list(entry.shared_modules),
            }
            for entry in explanation.historical_bugs
        ],
        "releases": [{"version": release.version} for release in explanation.releases],
        "known_data_gaps": list(explanation.data_gaps),
        "risk_already_computed_by_the_engine": {
            "score": assessment.score,
            "level": assessment.level,
            "breakdown": assessment.as_breakdown(),
        },
    }
    return (
        "Deterministic facts extracted from the project's own data follow. "
        "The risk score has already been computed from these signals.\n\n"
        f"```json\n{json.dumps(facts, indent=2, ensure_ascii=False, default=str)}\n```"
    )
