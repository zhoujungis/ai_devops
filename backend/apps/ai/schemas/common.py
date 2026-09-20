"""The shape every agent output must have.

Separating facts from hypotheses is not decoration: the product's whole claim is
that it tells you *what it knows* versus *what it suspects*, and a schema is the
only place that distinction can be enforced rather than merely requested.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ClaimType(StrEnum):
    FACT = "fact"
    EVIDENCE = "evidence"
    HYPOTHESIS = "hypothesis"


class EvidenceKind(StrEnum):
    """What an evidence reference points at. Used to resolve it before persisting."""

    COMMIT = "commit"
    MODULE = "module"
    TEST_CASE = "test_case"
    TEST_RUN = "test_run"
    BUG = "bug"
    REQUIREMENT = "requirement"
    RELEASE = "release"
    DOCUMENT_CHUNK = "document_chunk"
    LOG = "log"
    TRACE = "trace"


class StrictModel(BaseModel):
    """Rejects unknown keys.

    A model that invents a field has misunderstood the contract, and silently
    dropping it would hide that. Combined with the base provider's repair retry,
    strictness costs one extra round-trip at worst.
    """

    model_config = ConfigDict(extra="forbid")


class EvidenceRef(StrictModel):
    kind: EvidenceKind
    #: Must resolve to a real row in the requesting project; unresolved references
    #: are dropped rather than presented as evidence.
    ref_id: str
    note: str = ""


class AgentOutput(StrictModel):
    """The minimum every agent must return."""

    summary: str = ""
    #: 0..1. Required rather than defaulted so an agent cannot avoid stating it.
    confidence: float = Field(ge=0.0, le=1.0)
    facts: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    #: What a human should check to confirm or refute the hypotheses.
    how_to_verify: list[str] = Field(default_factory=list)
    #: What the agent could not determine. Stated explicitly so an incomplete
    #: analysis is never mistaken for a clean one.
    data_gaps: list[str] = Field(default_factory=list)
