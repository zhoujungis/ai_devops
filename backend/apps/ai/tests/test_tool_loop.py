"""The tool loop.

These are the tests that make two README claims true rather than aspirational: the
model is shown *only* read-only tools, and every tool call it makes is executed,
traced, and fed back — including the ones that reach outside the project, which are
recorded as denials instead of being swallowed.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from django.test import override_settings

from apps.accounts.models import Organization, Project
from apps.accounts.tests.factories import (
    MembershipFactory,
    OrganizationFactory,
    ProjectFactory,
    UserFactory,
)
from apps.ai.models import AIAnalysisJob, AIFinding, AIProviderConfig, AIToolCall
from apps.ai.providers.base import ToolCall
from apps.ai.providers.registry import ResolvedModel
from apps.ai.tasks import create_job, run_analysis_job
from apps.ai.tests.fakes import FakeAIProvider, make_tool_call
from apps.codebase.ingest import ingest_commit
from apps.integrations.tests.factories import build_repository
from apps.integrations.tests.fakes import make_remote_commit, make_remote_file
from apps.requirements.models import Requirement

pytestmark = pytest.mark.django_db

#: A valid RequirementOutput, so the agent has something to answer with.
REPLY = json.dumps(
    {
        "summary": "The requirement is mostly clear.",
        "confidence": 0.6,
        "facts": ["It states three retries."],
        "evidence": [],
        "hypotheses": [],
        "how_to_verify": [],
        "data_gaps": [],
        "risks": [{"risk": "Retry budget unclear", "level": "medium", "reason": "no cap stated"}],
        "test_points": ["retry then succeed"],
        "missing_scenarios": [
            {"scenario": "all retries exhausted", "why": "not stated", "category": "exception"}
        ],
        "ambiguities": [],
        "suggested_acceptance_criteria": ["A capture is retried at most three times."],
        "suggested_module_paths": ["src/payment"],
    }
)


def _scene() -> tuple[Project, Requirement]:
    org = cast(Organization, OrganizationFactory())
    MembershipFactory(org=org, user=UserFactory(), role="admin")
    project = cast(Project, ProjectFactory(org=org))
    repository = build_repository(project)
    ingest_commit(
        repository,
        make_remote_commit("abc123", minutes_ago=5, files=[make_remote_file("src/payment/a.py")]),
    )
    requirement = Requirement.objects.create(
        project=project, external_key="PAY-18", title="Retry failed payments"
    )
    return project, requirement


def _install(monkeypatch: Any, project: Project, provider: FakeAIProvider) -> None:
    config = AIProviderConfig.objects.create(
        org=project.org,
        provider_type="openai",
        label="Test",
        is_default=True,
        capability_models={"chat": "gpt-4o"},
    )
    monkeypatch.setattr(
        "apps.ai.tasks.resolve",
        lambda org, capability: ResolvedModel(config=config, provider=provider, model="gpt-4o"),
    )


def _run(project: Project, requirement: Requirement) -> AIAnalysisJob:
    job, _ = create_job(
        project=project,
        agent_code="requirement_analysis",
        target_type="requirement",
        target_id=str(requirement.pk),
    )
    run_analysis_job(str(job.pk))
    job.refresh_from_db()
    return job


def test_the_model_is_offered_only_the_read_only_tools(monkeypatch: Any) -> None:
    project, requirement = _scene()
    provider = FakeAIProvider(replies=[REPLY])
    _install(monkeypatch, project, provider)

    _run(project, requirement)

    offered = provider.tool_lists[0]
    assert offered, "the model must be given the tools, not just told about them"
    names = {spec["function"]["name"] for spec in offered}
    assert "get_commit" in names
    assert "create_test_case" not in names, "a write tool must never be exposed"


def test_a_tool_call_is_executed_traced_and_fed_back(monkeypatch: Any) -> None:
    project, requirement = _scene()
    provider = FakeAIProvider(
        replies=["{}", REPLY],
        tool_calls=[(make_tool_call("get_commit", {"sha": "abc123"}),)],
    )
    _install(monkeypatch, project, provider)

    job = _run(project, requirement)

    assert job.status == "succeeded", job.error
    # The call is recorded against the run that asked for it.
    call = AIToolCall.objects.get(run__job=job)
    assert call.tool_name == "get_commit"
    assert call.status == "ok"
    assert call.scope_denied is False
    # The result went back to the model as a tool message.
    second_call = provider.calls[1]
    assert second_call[-1].role == "tool"
    assert "abc123" in second_call[-1].content
    # One run per model interaction: the one that asked, and the one that answered.
    assert job.runs.count() == 2
    assert AIFinding.objects.filter(project=project).exists()


def test_a_refused_tool_is_recorded_as_denied_without_failing_the_job(
    monkeypatch: Any,
) -> None:
    """Reaching outside the project is an incident to record, not a crash."""
    project, requirement = _scene()
    provider = FakeAIProvider(
        replies=["{}", REPLY],
        tool_calls=[(make_tool_call("get_commit", {"sha": "not-in-this-project"}),)],
    )
    _install(monkeypatch, project, provider)

    job = _run(project, requirement)

    assert job.status == "succeeded", job.error
    call = AIToolCall.objects.get(run__job=job)
    assert call.scope_denied is True
    assert call.status == "denied"
    # The model is told what went wrong rather than being handed an empty result.
    assert "error" in provider.calls[1][-1].content


def test_the_iteration_bound_ends_the_loop_without_failing_the_analysis(
    monkeypatch: Any,
) -> None:
    """The bound is a budget guard: the model is cut off, not the job failed."""
    project, requirement = _scene()
    rounds: list[tuple[ToolCall, ...]] = [
        (make_tool_call("list_modules", {}, f"call-{index}"),) for index in range(3)
    ]
    provider = FakeAIProvider(replies=["{}", "{}", "{}", REPLY], tool_calls=rounds)
    _install(monkeypatch, project, provider)

    with override_settings(AI_ANALYSIS_MAX_TOOL_ITERATIONS=2):
        job = _run(project, requirement)

    assert job.status == "succeeded", job.error
    # Two bounded tool rounds plus the answering call.
    assert job.runs.count() == 3
    assert AIToolCall.objects.filter(run__job=job).count() == 2
