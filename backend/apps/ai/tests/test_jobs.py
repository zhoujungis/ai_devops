"""The async job framework, exercised with a stub agent.

S4 ships the machinery, not the agents. Proving the machinery works is what these
tests do: register a stub, run a job end to end, and assert the lifecycle, the stored
result and the failure modes.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

import pytest
from django.utils import timezone

from apps.accounts.models import Organization, Project
from apps.accounts.tests.factories import (
    MembershipFactory,
    OrganizationFactory,
    ProjectFactory,
    UserFactory,
)
from apps.ai.agents.base import AGENTS, BaseAgent, UnknownAgentError, agent_for
from apps.ai.models import AIAnalysisJob, AIProviderConfig, JobStatus
from apps.ai.tasks import create_job, fail_stale_jobs, run_analysis_job

pytestmark = pytest.mark.django_db


class StubAgent(BaseAgent):
    """Records what it was handed, so the framework's wiring is observable."""

    code = "stub"
    prompt_id = "code_impact"
    capability = "chat"
    description = "test double"

    def run(
        self,
        job: AIAnalysisJob,
        *,
        context: Any,
        provider: Any,
        model: str,
    ) -> dict[str, Any]:
        return {
            "target_id": job.target_id,
            "model": model,
            "project": context.project.name,
            "provider_type": provider.provider_type,
        }


@pytest.fixture
def project(db: Any) -> Project:
    org = cast(Organization, OrganizationFactory())
    MembershipFactory(org=org, user=UserFactory(), role="admin")
    return cast(Project, ProjectFactory(org=org))


@pytest.fixture
def stub_agent() -> Any:
    AGENTS[StubAgent.code] = StubAgent
    yield StubAgent
    AGENTS.pop(StubAgent.code, None)


@pytest.fixture
def provider_config(project: Project) -> AIProviderConfig:
    return AIProviderConfig.objects.create(
        org=project.org,
        provider_type="openai",
        label="Test provider",
        is_default=True,
        capability_models={"chat": "gpt-4o"},
    )


# ---------------------------------------------------------------------------
def test_create_job_is_idempotent_on_the_key(project: Project) -> None:
    first, created = create_job(project=project, agent_code="stub", idempotency_key="abc")
    second, created_again = create_job(project=project, agent_code="stub", idempotency_key="abc")

    assert created is True
    assert created_again is False
    assert first.pk == second.pk
    assert AIAnalysisJob.objects.filter(project=project).count() == 1


def test_create_job_without_a_key_always_creates(project: Project) -> None:
    first, _ = create_job(project=project, agent_code="stub")
    second, _ = create_job(project=project, agent_code="stub")

    assert first.pk != second.pk


def test_a_job_runs_to_completion_and_stores_its_result(
    project: Project, stub_agent: Any, provider_config: AIProviderConfig
) -> None:
    job, _ = create_job(
        project=project, agent_code="stub", target_type="commit", target_id="abc123"
    )

    outcome = run_analysis_job(str(job.pk))

    assert outcome["status"] == "succeeded"
    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED
    assert job.progress == 1.0
    assert job.started_at is not None and job.finished_at is not None
    assert job.result["target_id"] == "abc123"
    assert job.result["model"] == "gpt-4o"
    assert job.result["project"] == project.name


def test_an_unknown_agent_fails_the_job_without_retrying(
    project: Project, provider_config: AIProviderConfig
) -> None:
    job, _ = create_job(project=project, agent_code="not_registered")

    outcome = run_analysis_job(str(job.pk))

    assert outcome["status"] == "failed"
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert "No agent registered" in job.error
    assert job.finished_at is not None


def test_a_missing_provider_fails_the_job_with_a_usable_message(
    project: Project, stub_agent: Any
) -> None:
    """No provider configured is a setup problem, and the message must say so."""
    job, _ = create_job(project=project, agent_code="stub")

    outcome = run_analysis_job(str(job.pk))

    assert outcome["status"] == "failed"
    job.refresh_from_db()
    assert "No AI provider is configured" in job.error


def test_a_finished_job_is_not_run_twice(
    project: Project, stub_agent: Any, provider_config: AIProviderConfig
) -> None:
    job, _ = create_job(project=project, agent_code="stub")
    run_analysis_job(str(job.pk))

    outcome = run_analysis_job(str(job.pk))

    assert outcome["status"] == "already_finished"


def test_an_unknown_job_id_is_handled(project: Project) -> None:
    outcome = run_analysis_job("00000000-0000-0000-0000-000000000000")

    assert outcome["status"] == "missing"


def test_agent_lookup_lists_what_is_registered(stub_agent: Any) -> None:
    assert agent_for("stub").code == "stub"

    with pytest.raises(UnknownAgentError, match="Available"):
        agent_for("nope")


# ---------------------------------------------------------------------------
# reconciliation of jobs that will never finish
# ---------------------------------------------------------------------------
def test_the_sweeper_fails_a_job_that_was_never_picked_up(project: Project) -> None:
    """A broker outage at enqueue time leaves a QUEUED job no worker knows about."""
    stale, _ = create_job(project=project, agent_code="stub")
    AIAnalysisJob.objects.filter(pk=stale.pk).update(
        created_at=timezone.now() - timedelta(hours=2)
    )
    fresh, _ = create_job(project=project, agent_code="stub")

    outcome = fail_stale_jobs()

    assert outcome["queued"] == 1
    stale.refresh_from_db()
    fresh.refresh_from_db()
    assert stale.status == JobStatus.FAILED
    assert "Never picked up" in stale.error
    assert stale.finished_at is not None
    assert fresh.status == JobStatus.QUEUED, "a recent job must be left alone"


def test_the_sweeper_fails_a_job_that_outlived_the_time_limit(project: Project) -> None:
    job, _ = create_job(project=project, agent_code="stub")
    AIAnalysisJob.objects.filter(pk=job.pk).update(
        status=JobStatus.RUNNING, started_at=timezone.now() - timedelta(hours=3)
    )

    outcome = fail_stale_jobs()

    assert outcome["running"] == 1
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert "time limit" in job.error
