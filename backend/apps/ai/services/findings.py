"""Persisting what an agent concluded — after checking its evidence.

An agent asserts things. Before any assertion reaches the database, every reference
it cites is resolved against the requesting project. A reference that does not exist
is dropped rather than stored, because an unresolvable citation is worse than no
citation: it looks like proof while being uncheckable.
"""

from __future__ import annotations

import hashlib
from typing import Any

from django.core.exceptions import ValidationError

from apps.ai.models import AIAnalysisJob, AIFinding, FindingSeverity
from apps.ai.schemas.common import EvidenceKind, EvidenceRef
from apps.bugs.models import Bug
from apps.codebase.models import Commit, Module
from apps.releases.models import Release
from apps.requirements.models import Requirement
from apps.testing.models import TestCase, TestRun

#: Which model each evidence kind resolves against, and how to scope it to a project.
#: Typed loosely because this table exists precisely to be used generically.
_EVIDENCE_SOURCES: dict[str, tuple[type[Any], str]] = {
    EvidenceKind.COMMIT: (Commit, "repository__project"),
    EvidenceKind.MODULE: (Module, "project"),
    EvidenceKind.TEST_CASE: (TestCase, "project"),
    EvidenceKind.TEST_RUN: (TestRun, "project"),
    EvidenceKind.BUG: (Bug, "project"),
    EvidenceKind.REQUIREMENT: (Requirement, "project"),
    EvidenceKind.RELEASE: (Release, "project"),
}

#: Kinds we accept in the schema but cannot resolve yet. Named so the reason is
#: visible in the dropped list rather than looking like a bug.
UNRESOLVABLE_KINDS: dict[str, str] = {
    EvidenceKind.DOCUMENT_CHUNK: "knowledge base is not built yet",
    EvidenceKind.LOG: "log ingestion is not built yet",
    EvidenceKind.TRACE: "trace ingestion is not built yet",
}


def resolve_evidence(
    project: Any, refs: list[EvidenceRef]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return ``(kept, dropped_reasons)``.

    ``dropped_reasons`` is returned rather than logged so a caller can surface it —
    a model citing things that do not exist is worth knowing about.
    """
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []

    for ref in refs:
        if ref.kind in UNRESOLVABLE_KINDS:
            dropped.append(f"{ref.kind}:{ref.ref_id} — {UNRESOLVABLE_KINDS[ref.kind]}")
            continue

        source = _EVIDENCE_SOURCES.get(ref.kind)
        if source is None:
            dropped.append(f"{ref.kind}:{ref.ref_id} — unknown evidence kind")
            continue

        model, scope_path = source
        try:
            exists = model.objects.filter(pk=ref.ref_id, **{scope_path: project}).exists()
        except (ValidationError, ValueError):
            exists = False

        if not exists:
            dropped.append(f"{ref.kind}:{ref.ref_id} — not found in this project")
            continue

        kept.append({"kind": ref.kind, "ref_id": ref.ref_id, "note": ref.note})

    return kept, dropped


def finding_dedupe_hash(*, agent_code: str, target_type: str, target_id: str, title: str) -> str:
    """Identity of a finding, so re-analysis updates rather than duplicates."""
    material = f"{agent_code}|{target_type}|{target_id}|{title}".encode()
    return hashlib.sha256(material).hexdigest()


def record_finding(
    *,
    project: Any,
    job: AIAnalysisJob,
    agent_code: str,
    title: str,
    summary: str = "",
    payload: dict[str, Any] | None = None,
    confidence: float = 0.0,
    evidence: list[dict[str, Any]] | None = None,
    severity: str = FindingSeverity.MEDIUM,
    category: str = "",
) -> AIFinding:
    dedupe_hash = finding_dedupe_hash(
        agent_code=agent_code,
        target_type=job.target_type,
        target_id=job.target_id,
        title=title,
    )
    finding, _ = AIFinding.objects.update_or_create(
        project=project,
        dedupe_hash=dedupe_hash,
        defaults={
            "job": job,
            "agent_code": agent_code,
            "severity": severity,
            "category": category,
            "title": title[:500],
            "summary": summary,
            "payload": payload or {},
            "confidence": confidence,
            "evidence": evidence or [],
        },
    )
    return finding
