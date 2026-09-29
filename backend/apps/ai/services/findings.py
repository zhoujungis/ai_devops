"""Persisting what an agent concluded — after checking its evidence.

An agent asserts things. Before any assertion reaches the database, every reference
it cites is resolved against the requesting project. A reference that does not exist
is dropped rather than stored, because an unresolvable citation is worse than no
citation: it looks like proof while being uncheckable.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

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


def _as_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def resolve_evidence(
    project: Any, refs: list[EvidenceRef]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return ``(kept, dropped_reasons)``.

    ``dropped_reasons`` is returned rather than logged so a caller can surface it —
    a model citing things that do not exist is worth knowing about.

    Resolution is batched one query per evidence kind rather than one per reference:
    an agent that cites twenty modules should cost one query, not twenty. Ids are
    parsed up front so a single malformed value cannot fail a whole batch.
    """
    parsed = [_as_uuid(ref.ref_id) for ref in refs]
    dropped: list[str] = []

    for ref, value in zip(refs, parsed, strict=True):
        if ref.kind in UNRESOLVABLE_KINDS:
            dropped.append(f"{ref.kind}:{ref.ref_id} — {UNRESOLVABLE_KINDS[ref.kind]}")
        elif ref.kind not in _EVIDENCE_SOURCES:
            dropped.append(f"{ref.kind}:{ref.ref_id} — unknown evidence kind")
        elif value is None:
            dropped.append(f"{ref.kind}:{ref.ref_id} — not a valid id")

    found: dict[str, set[uuid.UUID]] = {}
    for kind in {ref.kind for ref in refs} & set(_EVIDENCE_SOURCES):
        ids = [value for ref, value in zip(refs, parsed, strict=True) if ref.kind == kind and value is not None]
        if not ids:
            found[kind] = set()
            continue
        model, scope_path = _EVIDENCE_SOURCES[kind]
        found[kind] = set(
            model.objects.filter(pk__in=ids, **{scope_path: project}).values_list("pk", flat=True)
        )

    kept: list[dict[str, Any]] = []
    for ref, value in zip(refs, parsed, strict=True):
        if value is None or ref.kind not in _EVIDENCE_SOURCES:
            continue  # already reported above
        if value in found[ref.kind]:
            kept.append({"kind": ref.kind, "ref_id": ref.ref_id, "note": ref.note})
        else:
            dropped.append(f"{ref.kind}:{ref.ref_id} — not found in this project")

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
