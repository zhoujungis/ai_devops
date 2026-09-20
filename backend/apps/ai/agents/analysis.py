"""Shared pipeline for the agents that read facts, ask a model, and record the answer.

Each agent differs only in *what facts it gathers* and *what it proposes*. Everything
else — loading the versioned prompt, calling the model with its schema, tracing the
call, validating the evidence, persisting the finding — is identical, and lives here
so it cannot drift between agents.
"""

from __future__ import annotations

import json
from abc import abstractmethod
from typing import Any, ClassVar

from apps.ai.agents.base import AgentError, BaseAgent
from apps.ai.models import AIAnalysisJob, AIFinding, FindingSeverity
from apps.ai.prompts.registry import load_prompt
from apps.ai.providers.base import AIProvider, ChatMessage
from apps.ai.schemas.common import AgentOutput
from apps.ai.services.findings import record_finding, resolve_evidence
from apps.ai.services.tracing import record_run
from apps.ai.tools.base import ToolContext


class AnalysisAgent(BaseAgent):
    """An agent whose output is a finding, and optionally a proposal."""

    #: The pydantic model the prompt's frontmatter must name.
    schema: ClassVar[type[AgentOutput]]
    finding_category: ClassVar[str] = ""
    default_severity: ClassVar[str] = FindingSeverity.MEDIUM

    # ------------------------------------------------------------------
    def run(
        self,
        job: AIAnalysisJob,
        *,
        context: ToolContext,
        provider: AIProvider,
        model: str,
    ) -> dict[str, Any]:
        facts, headline = self.gather(job, context)
        if facts is None:
            raise AgentError(f"{self.code}: nothing to analyse for target {job.target_id!r}.")

        template = load_prompt(self.prompt_id)
        messages = [
            ChatMessage(role="system", content=template.system),
            ChatMessage(role="user", content=_render(facts)),
        ]

        output, result = provider.structured_output(messages, schema=self.schema, model=model)

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
            title=f"{headline}: {output.summary[:120]}" if output.summary else headline,
            summary=output.summary,
            payload={
                "output": output.model_dump(mode="json"),
                "dropped_evidence": dropped,
            },
            confidence=output.confidence,
            evidence=kept_evidence,
            severity=self.default_severity,
            category=self.finding_category or self.code,
        )

        recommendations = self.propose(output=output, job=job, context=context, finding=finding)

        return {
            "finding_id": str(finding.pk),
            "analysis": output.model_dump(mode="json"),
            "recommendation_ids": [item["id"] for item in recommendations],
            "recommendations": recommendations,
            "dropped_evidence": dropped,
            "data_gaps": list(output.data_gaps),
        }

    # ------------------------------------------------------------------
    @abstractmethod
    def gather(self, job: AIAnalysisJob, context: ToolContext) -> tuple[dict[str, Any] | None, str]:
        """Collect the deterministic facts, and a short headline for the finding.

        Returning ``None`` as the facts means the target could not be resolved, which
        fails the job rather than asking the model to invent something.
        """

    def propose(
        self,
        *,
        output: AgentOutput,
        job: AIAnalysisJob,
        context: ToolContext,
        finding: AIFinding,
    ) -> list[dict[str, str]]:
        """Optionally turn the analysis into a proposal. Default: nothing to propose."""
        return []


def _render(facts: dict[str, Any]) -> str:
    return (
        "Deterministic facts extracted from the project's own data follow.\n\n"
        f"```json\n{json.dumps(facts, indent=2, ensure_ascii=False, default=str)}\n```"
    )
