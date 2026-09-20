"""Pydantic schemas: what an agent is allowed to return."""

from apps.ai.schemas.bug_investigation import BugInvestigationOutput, RcaOutput
from apps.ai.schemas.code_impact import CodeImpactOutput
from apps.ai.schemas.common import (
    AgentOutput,
    ClaimType,
    EvidenceKind,
    EvidenceRef,
    StrictModel,
)
from apps.ai.schemas.requirement import RequirementOutput
from apps.ai.schemas.test_generation import TestGenerationOutput

#: prompt id -> output schema. The prompt registry names a schema; this is where
#: that name is resolved, so a prompt can never reference a schema that is not
#: registered.
SCHEMAS: dict[str, type[StrictModel]] = {
    "CodeImpactOutput": CodeImpactOutput,
    "RequirementOutput": RequirementOutput,
    "TestGenerationOutput": TestGenerationOutput,
    "BugInvestigationOutput": BugInvestigationOutput,
    "RcaOutput": RcaOutput,
}

__all__ = [
    "SCHEMAS",
    "AgentOutput",
    "BugInvestigationOutput",
    "ClaimType",
    "CodeImpactOutput",
    "EvidenceKind",
    "EvidenceRef",
    "RcaOutput",
    "RequirementOutput",
    "StrictModel",
    "TestGenerationOutput",
]
