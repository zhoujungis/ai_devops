"""Requirement analysis output."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from apps.ai.schemas.common import AgentOutput, StrictModel


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RequirementRisk(StrictModel):
    risk: str
    level: RiskLevel
    reason: str


class MissingScenario(StrictModel):
    scenario: str
    why: str
    #: One of: normal, exception, boundary, null, type_error, permission,
    #: concurrency, duplicate, timeout, network, data_anomaly.
    category: str = ""


class Ambiguity(StrictModel):
    question: str
    impact: str = ""


class RequirementOutput(AgentOutput):
    risks: list[RequirementRisk] = Field(default_factory=list)
    test_points: list[str] = Field(default_factory=list)
    missing_scenarios: list[MissingScenario] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    suggested_acceptance_criteria: list[str] = Field(default_factory=list)
    suggested_module_paths: list[str] = Field(default_factory=list)
