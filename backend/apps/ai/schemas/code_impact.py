"""What the Code Impact agent is allowed to return.

Note what is absent: **there is no risk score here**. The score is computed by the
risk engine from measurable signals; the model's job is to explain that score and
surface things the signals cannot see, not to invent a number. If the model could
return a score, two sources of truth would exist and the explainable one would lose.
"""

from __future__ import annotations

from pydantic import Field

from apps.ai.schemas.common import AgentOutput, StrictModel


class ModuleImpactClaim(StrictModel):
    """How much a changed module matters, and why the model says so."""

    module: str
    impact: int = Field(ge=1, le=5)
    reason: str


class AffectedFeature(StrictModel):
    feature: str
    confidence: float = Field(ge=0.0, le=1.0)
    #: How the model got from a module to this feature, e.g. "module->requirement".
    path: str = ""


class RegressionSuggestion(StrictModel):
    key: str
    title: str = ""
    #: 0..1. How strongly this case should be re-run for this change.
    score: float = Field(ge=0.0, le=1.0)
    reason: str


class SuspectedPattern(StrictModel):
    pattern: str
    #: A historical bug key, when the suspicion is grounded in one.
    historical_bug: str = ""


class CodeImpactOutput(AgentOutput):
    changed_modules: list[ModuleImpactClaim] = Field(default_factory=list)
    affected_features: list[AffectedFeature] = Field(default_factory=list)
    potential_impacts: list[str] = Field(default_factory=list)
    recommended_regression_tests: list[RegressionSuggestion] = Field(default_factory=list)
    suspected_bug_patterns: list[SuspectedPattern] = Field(default_factory=list)
    #: Prose explaining the engine's score. Never a score of its own.
    risk_explanation: str = ""
