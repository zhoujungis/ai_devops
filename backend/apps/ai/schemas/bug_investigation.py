"""Bug investigation and root-cause analysis output.

The wording rules the plan insists on — never "the root cause is X" — are encoded
here as structure. A cause carries a confidence, its evidence, and the steps that
would confirm or refute it; there is no field for a bare assertion.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from apps.ai.schemas.common import AgentOutput, StrictModel


class CauseConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PossibleCause(StrictModel):
    cause: str
    confidence: CauseConfidence
    #: Why we believe it. Never empty: a cause without stated reasoning is a guess.
    reasoning: str
    affected_components: list[str] = Field(default_factory=list)
    #: What a person should check to move this from "possible" to "known".
    how_to_verify: list[str] = Field(default_factory=list)


class RelatedChange(StrictModel):
    commit: str
    module: str = ""
    released_at: str = ""
    relevance: str = ""


class SimilarIssue(StrictModel):
    bug: str
    similarity: float = Field(ge=0.0, le=1.0)
    resolution: str = ""


class RcaOutput(AgentOutput):
    """Root-cause candidates, ranked, each with its own evidence and checks."""

    candidates: list[PossibleCause] = Field(default_factory=list)
    timeline: list[str] = Field(default_factory=list)
    affected_components: list[str] = Field(default_factory=list)


class BugInvestigationOutput(AgentOutput):
    related_changes: list[RelatedChange] = Field(default_factory=list)
    historical_similar_issues: list[SimilarIssue] = Field(default_factory=list)
    possible_causes: list[PossibleCause] = Field(default_factory=list)
    recommended_verification: list[str] = Field(default_factory=list)
    recommended_regression_tests: list[str] = Field(default_factory=list)
    #: The chain the agent actually walked, so a reader can see how it got here.
    investigation_chain: list[str] = Field(default_factory=list)
