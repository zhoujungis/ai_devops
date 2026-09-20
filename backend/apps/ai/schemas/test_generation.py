"""Test generation output.

The coverage matrix is required rather than optional: the value of generated cases is
that they are *systematically* varied, and a model left to its own devices produces
three variations of the happy path. Forcing it to state a verdict for all eleven
scenario categories makes the gaps visible instead of absent.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from apps.ai.schemas.common import AgentOutput, StrictModel

#: The categories every generation must account for.
SCENARIO_CATEGORIES: tuple[str, ...] = (
    "normal",
    "exception",
    "boundary",
    "null",
    "type_error",
    "permission",
    "concurrency",
    "duplicate",
    "timeout",
    "network",
    "data_anomaly",
)


class ScenarioType(StrEnum):
    NORMAL = "normal"
    EXCEPTION = "exception"
    BOUNDARY = "boundary"
    NULL = "null"
    TYPE_ERROR = "type_error"
    PERMISSION = "permission"
    CONCURRENCY = "concurrency"
    DUPLICATE = "duplicate"
    TIMEOUT = "timeout"
    NETWORK = "network"
    DATA_ANOMALY = "data_anomaly"


class Priority(StrEnum):
    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"


class GeneratedStep(StrictModel):
    action: str
    data: dict[str, str] = Field(default_factory=dict)


class AutomationSuggestion(StrictModel):
    type: str = "api"
    framework: str = ""
    sketch: str = ""


class GeneratedTestCase(StrictModel):
    title: str
    scenario_type: ScenarioType
    priority: Priority = Priority.P2
    precondition: list[str] = Field(default_factory=list)
    steps: list[GeneratedStep] = Field(default_factory=list)
    expected: str
    tags: list[str] = Field(default_factory=list)
    #: Module path prefixes this case should be linked to, so it becomes part of the
    #: regression chain rather than an orphan.
    module_path_prefixes: list[str] = Field(default_factory=list)
    automation_suggestion: AutomationSuggestion | None = None
    requirement_key: str = ""


class CoverageVerdict(StrictModel):
    """What the model concluded about one scenario category."""

    category: str
    covered: bool
    #: Required when ``covered`` is false, so "not applicable" is argued rather than
    #: assumed.
    rationale: str = ""


class TestGenerationOutput(AgentOutput):
    test_cases: list[GeneratedTestCase] = Field(default_factory=list)
    coverage_matrix: list[CoverageVerdict] = Field(default_factory=list)
    #: Cases the model considered but decided already exist, by key or title.
    skipped_as_duplicate: list[str] = Field(default_factory=list)
