"""The confirm-before-execute flow, end to end.

This is the S6 acceptance test, and it asserts the promises that make an AI that
changes things acceptable at all:

* proposing creates no domain rows;
* approving creates exactly what was approved, and leaves an audit record;
* rejecting writes nothing to the domain at all;
* reaching outside the project is recorded as a denied attempt.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from apps.accounts.models import Organization, Project
from apps.accounts.tests.factories import (
    MembershipFactory,
    OrganizationFactory,
    ProjectFactory,
    UserFactory,
)
from apps.ai.agents.base import AGENTS, agent_for
from apps.ai.models import (
    AIConfirmation,
    AIFinding,
    AIProviderConfig,
    AIRecommendation,
    AuditLog,
    RecommendationStatus,
)
from apps.ai.providers.registry import ResolvedModel
from apps.ai.services.confirmation import (
    ConfirmationError,
    execute_recommendation,
    reject_recommendation,
)
from apps.ai.services.tooling import run_tool
from apps.ai.services.tracing import record_run
from apps.ai.tasks import create_job, run_analysis_job
from apps.ai.tests.fakes import FakeAIProvider
from apps.ai.tools.base import ToolContext, ToolScopeError
from apps.ai.tools.registry import load_default_tools
from apps.bugs.models import Bug
from apps.codebase.ingest import ingest_commit
from apps.codebase.models import Module
from apps.integrations.tests.factories import build_repository
from apps.integrations.tests.fakes import make_remote_commit, make_remote_file
from apps.requirements.models import Requirement
from apps.testing.models import TestCase

pytestmark = pytest.mark.django_db

PAYMENT_FILE = "src/payment/PaymentService.java"


def _generation_reply(count: int = 3) -> str:
    return json.dumps(
        {
            "summary": "Proposed cases for the retry requirement.",
            "confidence": 0.7,
            "facts": ["The requirement mentions three retries."],
            "evidence": [],
            "hypotheses": [],
            "how_to_verify": [],
            "data_gaps": [],
            "test_cases": [
                {
                    "title": f"Retry case {index}",
                    "scenario_type": "exception",
                    "priority": "p1",
                    "precondition": ["A capture is pending"],
                    "steps": [{"action": "Force a timeout", "data": {"attempt": str(index)}}],
                    "expected": "The capture is retried and eventually fails cleanly.",
                    "tags": ["payment"],
                    "module_path_prefixes": ["src/payment"],
                    "requirement_key": "PAY-18",
                }
                for index in range(1, count + 1)
            ],
            "coverage_matrix": [
                {"category": "normal", "covered": True, "rationale": ""},
                {"category": "timeout", "covered": True, "rationale": ""},
                {"category": "permission", "covered": False, "rationale": "no auth in this path"},
            ],
            "skipped_as_duplicate": [],
        }
    )


@pytest.fixture
def scene() -> tuple[Project, Requirement]:
    org = cast(Organization, OrganizationFactory())
    MembershipFactory(org=org, user=UserFactory(), role="admin")
    project = cast(Project, ProjectFactory(org=org))
    repository = build_repository(project)
    ingest_commit(
        repository,
        make_remote_commit("abc123", minutes_ago=5, files=[make_remote_file(PAYMENT_FILE)]),
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


def _run_generation(
    project: Project, requirement: Requirement, *, count: int = 3
) -> AIRecommendation:
    job, _ = create_job(
        project=project,
        agent_code="test_generation",
        target_type="requirement",
        target_id=str(requirement.pk),
    )
    run_analysis_job(str(job.pk))
    return AIRecommendation.objects.get(project=project)


# ---------------------------------------------------------------------------
def test_proposing_creates_no_test_cases(
    monkeypatch: Any, scene: tuple[Project, Requirement]
) -> None:
    project, requirement = scene
    _install(monkeypatch, project, FakeAIProvider(replies=[_generation_reply(3)]))

    recommendation = _run_generation(project, requirement, count=3)

    assert (
        TestCase.objects.filter(project=project).count() == 0
    ), "an unconfirmed proposal must not create rows"
    assert recommendation.status == RecommendationStatus.PENDING
    assert len(recommendation.payload["test_cases"]) == 3
    assert recommendation.finding is not None


def test_approving_creates_exactly_what_was_approved(
    monkeypatch: Any, scene: tuple[Project, Requirement]
) -> None:
    project, requirement = scene
    _install(monkeypatch, project, FakeAIProvider(replies=[_generation_reply(3)]))
    recommendation = _run_generation(project, requirement, count=3)
    user = cast(Any, UserFactory())
    MembershipFactory(org=project.org, user=user, role="admin")

    execute_recommendation(recommendation, user=user)

    cases = TestCase.objects.filter(project=project).order_by("key")
    assert cases.count() == 3
    assert [case.title for case in cases] == ["Retry case 1", "Retry case 2", "Retry case 3"]
    assert all(case.origin == "ai_generated" for case in cases)
    assert [case.key for case in cases] == ["TC-001", "TC-002", "TC-003"]

    recommendation.refresh_from_db()
    assert recommendation.status == RecommendationStatus.EXECUTED


def test_approving_links_the_cases_to_their_module(
    monkeypatch: Any, scene: tuple[Project, Requirement]
) -> None:
    project, requirement = scene
    _install(monkeypatch, project, FakeAIProvider(replies=[_generation_reply(2)]))
    recommendation = _run_generation(project, requirement, count=2)

    execute_recommendation(recommendation)

    module = Module.objects.get(project=project, path_prefix="src/payment")
    for case in TestCase.objects.filter(project=project):
        assert case.module_links.filter(
            module=module
        ).exists(), "an unlinked case falls out of the regression chain"


def test_approving_leaves_an_audit_record(
    monkeypatch: Any, scene: tuple[Project, Requirement]
) -> None:
    project, requirement = scene
    _install(monkeypatch, project, FakeAIProvider(replies=[_generation_reply(2)]))
    recommendation = _run_generation(project, requirement, count=2)
    user = cast(Any, UserFactory())

    execute_recommendation(recommendation, user=user, request_id="req-1")

    confirmation = AIConfirmation.objects.get(recommendation=recommendation)
    assert confirmation.decision == "confirmed"
    assert confirmation.executor_code == "create_test_cases"
    assert len(confirmation.result["created"]) == 2

    audit = AuditLog.objects.get(action="ai_recommendation.confirmed")
    assert audit.actor_type == "user"
    assert audit.target_id == str(recommendation.pk)
    assert audit.before["type"] == "create_test_cases"
    assert len(audit.after["created"]) == 2
    assert audit.request_id == "req-1"


def test_rejecting_writes_nothing_to_the_domain(
    monkeypatch: Any, scene: tuple[Project, Requirement]
) -> None:
    project, requirement = scene
    _install(monkeypatch, project, FakeAIProvider(replies=[_generation_reply(3)]))
    recommendation = _run_generation(project, requirement, count=3)
    user = cast(Any, UserFactory())

    reject_recommendation(recommendation, user=user, reason="duplicates TC-100")

    assert TestCase.objects.filter(project=project).count() == 0
    recommendation.refresh_from_db()
    assert recommendation.status == RecommendationStatus.REJECTED

    confirmation = AIConfirmation.objects.get(recommendation=recommendation)
    assert confirmation.decision == "rejected"
    assert confirmation.reason == "duplicates TC-100"
    assert confirmation.executor_code == ""
    assert AuditLog.objects.filter(action="ai_recommendation.rejected").exists()


def test_the_same_recommendation_cannot_be_executed_twice(
    monkeypatch: Any, scene: tuple[Project, Requirement]
) -> None:
    project, requirement = scene
    _install(monkeypatch, project, FakeAIProvider(replies=[_generation_reply(2)]))
    recommendation = _run_generation(project, requirement, count=2)
    execute_recommendation(recommendation)

    with pytest.raises(ConfirmationError, match="not pending"):
        execute_recommendation(recommendation)

    assert TestCase.objects.filter(project=project).count() == 2


def test_a_failed_execution_is_recorded_and_leaves_no_partial_rows(
    monkeypatch: Any, scene: tuple[Project, Requirement]
) -> None:
    """A failed executor must be traceable, and must not half-apply.

    The bookkeeping has to survive the rollback of whatever the executor wrote;
    a single wrapping transaction would unwind both and leave a failure looking
    identical to "nothing ever happened".
    """
    project, requirement = scene
    _install(monkeypatch, project, FakeAIProvider(replies=[_generation_reply(3)]))
    recommendation = _run_generation(project, requirement, count=3)
    good, broken = recommendation.payload["test_cases"][:2]
    payload = {"test_cases": [good, {**broken, "title": ""}]}

    with pytest.raises(ConfirmationError):
        execute_recommendation(recommendation, edited_payload=payload)

    recommendation.refresh_from_db()
    assert recommendation.status == RecommendationStatus.FAILED
    assert AIConfirmation.objects.filter(
        recommendation=recommendation, decision="confirmed"
    ).exists()
    assert AuditLog.objects.filter(action="ai_recommendation.failed").exists()
    assert (
        TestCase.objects.filter(project=project).count() == 0
    ), "the first case must not survive a failure on the second"


def test_an_edited_payload_is_what_gets_created(
    monkeypatch: Any, scene: tuple[Project, Requirement]
) -> None:
    """A user may trim the proposal; the trimmed version is what is stored."""
    project, requirement = scene
    _install(monkeypatch, project, FakeAIProvider(replies=[_generation_reply(3)]))
    recommendation = _run_generation(project, requirement, count=3)
    trimmed = {"test_cases": recommendation.payload["test_cases"][:1]}

    execute_recommendation(recommendation, edited_payload=trimmed)

    assert TestCase.objects.filter(project=project).count() == 1
    confirmation = AIConfirmation.objects.get(recommendation=recommendation)
    assert len(confirmation.edited_payload["test_cases"]) == 1


def test_an_agent_cannot_propose_an_unregistered_action() -> None:
    org = cast(Organization, OrganizationFactory())
    project = cast(Project, ProjectFactory(org=org))

    from apps.ai.services.confirmation import propose

    with pytest.raises(ConfirmationError, match="not a registered action"):
        propose(
            project=project,
            agent_code="test_generation",
            type="delete_everything",
            title="Nope",
        )
    assert AIRecommendation.objects.count() == 0


# ---------------------------------------------------------------------------
# scope denial is recorded, not swallowed
# ---------------------------------------------------------------------------
def test_reaching_outside_the_project_is_recorded_as_denied(
    scene: tuple[Project, Requirement],
) -> None:
    project, _requirement = scene
    other_org = cast(Organization, OrganizationFactory())
    other_project = cast(Project, ProjectFactory(org=other_org))
    other_repository = build_repository(other_project)
    ingest_commit(
        other_repository,
        make_remote_commit("other-sha", minutes_ago=5, files=[make_remote_file(PAYMENT_FILE)]),
    )

    load_default_tools()
    job, _ = create_job(project=project, agent_code="test_generation")
    run = record_run(job=job, provider_type="openai", model="m", capability="chat", input_text="p")

    with pytest.raises(ToolScopeError):
        run_tool(
            run=run,
            tool_name="get_commit",
            arguments={"sha": "other-sha"},
            context=ToolContext(project=project),
        )

    call = run.tool_calls.get()
    assert call.scope_denied is True
    assert call.status == "denied"


def test_a_successful_tool_call_is_recorded_as_ok(scene: tuple[Project, Requirement]) -> None:
    project, _requirement = scene
    load_default_tools()
    job, _ = create_job(project=project, agent_code="test_generation")
    run = record_run(job=job, provider_type="openai", model="m", capability="chat", input_text="p")

    result = run_tool(
        run=run,
        tool_name="get_commit",
        arguments={"sha": "abc123"},
        context=ToolContext(project=project),
    )

    assert result.data["sha"] == "abc123"
    call = run.tool_calls.get()
    assert call.status == "ok"
    assert call.scope_denied is False
    assert call.result_summary


# ---------------------------------------------------------------------------
def test_every_built_in_agent_is_registered() -> None:
    assert set(AGENTS) >= {
        "code_impact",
        "requirement_analysis",
        "test_generation",
        "bug_investigation",
        "rca",
    }
    for code in ("requirement_analysis", "test_generation", "bug_investigation", "rca"):
        assert agent_for(code).description


def test_a_requirement_job_produces_findings_without_proposals(
    monkeypatch: Any, scene: tuple[Project, Requirement]
) -> None:
    project, requirement = scene
    reply = json.dumps(
        {
            "summary": "The requirement is mostly clear.",
            "confidence": 0.6,
            "facts": ["It states three retries."],
            "evidence": [],
            "hypotheses": [],
            "how_to_verify": [],
            "data_gaps": [],
            "risks": [
                {"risk": "Retry budget unclear", "level": "medium", "reason": "no cap stated"}
            ],
            "test_points": ["retry then succeed"],
            "missing_scenarios": [
                {"scenario": "all retries exhausted", "why": "not stated", "category": "exception"}
            ],
            "ambiguities": [],
            "suggested_acceptance_criteria": ["A capture is retried at most three times."],
            "suggested_module_paths": ["src/payment"],
        }
    )
    _install(monkeypatch, project, FakeAIProvider(replies=[reply]))
    job, _ = create_job(
        project=project,
        agent_code="requirement_analysis",
        target_type="requirement",
        target_id=str(requirement.pk),
    )

    outcome = run_analysis_job(str(job.pk))

    assert outcome["status"] == "succeeded", job.error
    finding = AIFinding.objects.get(project=project)
    assert finding.agent_code == "requirement_analysis"
    assert finding.payload["output"]["risks"][0]["risk"] == "Retry budget unclear"
    # The requirement agent analyses; it does not change anything.
    assert AIRecommendation.objects.count() == 0


def test_an_unresolvable_target_fails_the_job(
    monkeypatch: Any, scene: tuple[Project, Requirement]
) -> None:
    project, _requirement = scene
    _install(monkeypatch, project, FakeAIProvider(replies=[_generation_reply(1)]))
    job, _ = create_job(
        project=project,
        agent_code="test_generation",
        target_type="requirement",
        target_id="00000000-0000-0000-0000-000000000000",
    )

    outcome = run_analysis_job(str(job.pk))

    assert outcome["status"] == "failed"
    job.refresh_from_db()
    assert job.status == "failed"
    assert job.error


# ---------------------------------------------------------------------------
# generated keys follow the project's configured prefix
# ---------------------------------------------------------------------------
def test_generated_keys_use_the_project_prefix() -> None:
    from services.executors import _next_bug_key, _next_test_case_key

    project = cast(Project, ProjectFactory(org=OrganizationFactory(), key_prefix="PAY"))

    assert _next_test_case_key(project) == "PAY-001"
    assert _next_bug_key(project) == "PAY-1"


def test_generated_keys_fall_back_when_no_prefix_is_set() -> None:
    from services.executors import _next_bug_key, _next_test_case_key

    project = cast(Project, ProjectFactory(org=OrganizationFactory()))

    assert _next_test_case_key(project) == "TC-001"
    assert _next_bug_key(project) == "BUG-1"


def test_the_rca_agent_ranks_candidates_for_a_defect(
    monkeypatch: Any, scene: tuple[Project, Requirement]
) -> None:
    """`rca` reuses the bug investigation chain but answers a different question."""
    project, _requirement = scene
    bug = Bug.objects.create(project=project, key="BUG-1", title="502 on a cold upstream")
    reply = json.dumps(
        {
            "summary": "Two candidates, one better supported.",
            "confidence": 0.5,
            "facts": ["The retry path changed this week."],
            "evidence": [],
            "hypotheses": [],
            "how_to_verify": [],
            "data_gaps": ["No metrics were supplied."],
            "candidates": [
                {
                    "cause": "the retry budget is too small",
                    "confidence": "medium",
                    "reasoning": "the backoff change landed before the first sighting",
                    "how_to_verify": ["replay a 504 against staging"],
                }
            ],
            "timeline": ["deploy at 10:00", "first symptom at 10:05"],
            "affected_components": ["apps/gateway"],
        }
    )
    _install(monkeypatch, project, FakeAIProvider(replies=[reply]))
    job, _ = create_job(project=project, agent_code="rca", target_type="bug", target_id=bug.key)

    outcome = run_analysis_job(str(job.pk))

    assert outcome["status"] == "succeeded", job.error
    finding = AIFinding.objects.get(project=project, agent_code="rca")
    assert finding.payload["output"]["candidates"][0]["cause"] == "the retry budget is too small"
    # It analyses; it does not propose a change.
    assert AIRecommendation.objects.count() == 0
