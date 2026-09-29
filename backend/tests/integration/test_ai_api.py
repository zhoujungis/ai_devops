"""The AI and risk HTTP surface.

The tests that matter here are the ones about *shape*: that starting an analysis
returns a job rather than a result, that a viewer cannot confirm anything, that the
risk endpoint's breakdown adds up, and that another tenant's data is a 404 rather
than an error code that leaks its existence.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Organization, Project, User
from apps.accounts.roles import Role
from apps.accounts.tests.factories import (
    MembershipFactory,
    OrganizationFactory,
    ProjectFactory,
    UserFactory,
)
from apps.ai.models import (
    AIFinding,
    AIProviderConfig,
    AIRecommendation,
    AuditLog,
    RecommendationStatus,
)
from apps.ai.providers.registry import ResolvedModel
from apps.ai.tests.fakes import FakeAIProvider, make_tool_call
from apps.ai.tests.test_confirmation import _generation_reply
from apps.codebase.ingest import ingest_commit
from apps.codebase.models import Commit, Module
from apps.integrations.tests.factories import build_repository
from apps.integrations.tests.fakes import make_remote_commit, make_remote_file
from apps.requirements.models import Requirement
from apps.testing.models import TestCase

pytestmark = pytest.mark.django_db

PAYMENT_FILE = "src/payment/PaymentService.java"


def _scene(role: Role = Role.ADMIN) -> tuple[Organization, Project, User, Requirement]:
    org = cast(Organization, OrganizationFactory())
    user = cast(User, UserFactory())
    MembershipFactory(org=org, user=user, role=role)
    project = cast(Project, ProjectFactory(org=org))
    repository = build_repository(project)
    ingest_commit(
        repository,
        make_remote_commit("abc123", minutes_ago=5, files=[make_remote_file(PAYMENT_FILE)]),
    )
    requirement = Requirement.objects.create(
        project=project, external_key="PAY-18", title="Retry failed payments"
    )
    return org, project, user, requirement


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _install_provider(monkeypatch: Any, org: Organization, provider: FakeAIProvider) -> None:
    config = AIProviderConfig.objects.create(
        org=org,
        provider_type="openai",
        label="Test",
        is_default=True,
        capability_models={"chat": "gpt-4o"},
    )
    monkeypatch.setattr(
        "apps.ai.tasks.resolve",
        lambda organization, capability: ResolvedModel(
            config=config, provider=provider, model="gpt-4o"
        ),
    )


# ---------------------------------------------------------------------------
# analyses
# ---------------------------------------------------------------------------
def test_starting_an_analysis_returns_a_job_not_a_result(
    monkeypatch: Any,
) -> None:
    org, project, user, requirement = _scene()
    _install_provider(monkeypatch, org, FakeAIProvider(replies=[_generation_reply(2)]))

    response = _client(user).post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/analyses",
        {
            "agent": "test_generation",
            "target_type": "requirement",
            "target_id": str(requirement.pk),
        },
        format="json",
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] in {"queued", "running", "succeeded"}
    assert body["agent_code"] == "test_generation"
    assert body["project"] == str(project.pk)


def test_an_unknown_agent_is_rejected_with_the_available_list() -> None:
    org, project, user, _requirement = _scene()

    response = _client(user).post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/analyses",
        {"agent": "does_not_exist"},
        format="json",
    )

    assert response.status_code == 400
    details = response.json()["error"]["details"]
    assert "does_not_exist" in str(details) or "Available" in str(details)


def test_the_agents_endpoint_lists_what_can_be_run() -> None:
    org, project, user, _requirement = _scene()

    response = _client(user).get(f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/analyses/agents")

    assert response.status_code == 200
    codes = {row["code"] for row in response.json()}
    assert {"code_impact", "test_generation", "requirement_analysis"} <= codes


def test_a_repeated_idempotency_key_returns_the_same_job() -> None:
    org, project, user, requirement = _scene()
    url = f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/analyses"
    payload = {
        "agent": "test_generation",
        "target_type": "requirement",
        "target_id": str(requirement.pk),
        "idempotency_key": "same-key",
    }
    client = _client(user)

    first = client.post(url, payload, format="json").json()
    second = client.post(url, payload, format="json").json()

    assert first["id"] == second["id"]


def test_a_job_can_be_polled_and_carries_its_usage(monkeypatch: Any) -> None:
    org, project, user, requirement = _scene()
    _install_provider(monkeypatch, org, FakeAIProvider(replies=[_generation_reply(2)]))
    client = _client(user)

    job_id = client.post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/analyses",
        {
            "agent": "test_generation",
            "target_type": "requirement",
            "target_id": str(requirement.pk),
        },
        format="json",
    ).json()["id"]

    body = client.get(f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/jobs/{job_id}").json()

    assert body["result"] is not None
    assert body["usage"]["input_tokens"] == 100
    assert body["usage"]["runs"] >= 1
    assert body["findings"], "a successful analysis records a finding"


def test_a_job_trace_shows_the_model_runs_and_tool_calls_behind_it(monkeypatch: Any) -> None:
    """The job says what came out; the trace says how it got there."""
    org, project, user, requirement = _scene()
    _install_provider(
        monkeypatch,
        org,
        FakeAIProvider(
            replies=["{}", _generation_reply(2)],
            tool_calls=[(make_tool_call("list_modules", {}),)],
        ),
    )
    client = _client(user)

    job_id = client.post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/analyses",
        {
            "agent": "test_generation",
            "target_type": "requirement",
            "target_id": str(requirement.pk),
        },
        format="json",
    ).json()["id"]

    response = client.get(f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/jobs/{job_id}/trace")

    assert response.status_code == 200
    runs = response.json()
    # One interaction to look things up, one to answer.
    assert len(runs) == 2
    assert runs[0]["tool_calls"][0]["tool_name"] == "list_modules"
    assert runs[0]["tool_calls"][0]["status"] == "ok"
    assert runs[1]["tool_calls"] == []
    assert all(run["input_tokens"] == 100 for run in runs)


def test_provider_credentials_can_be_checked_on_demand(monkeypatch: Any) -> None:
    """A revoked key should be found here, not on the next analysis."""
    org, _project, user, _requirement = _scene()
    client = _client(user)
    config = AIProviderConfig.objects.create(
        org=org, provider_type="openai", label="Main", capability_models={"chat": "gpt-4o"}
    )
    fake = FakeAIProvider(verify_result=False)
    monkeypatch.setattr("apps.ai.views.build_provider", lambda config: fake)

    response = client.post(f"/api/v1/orgs/{org.pk}/ai-providers/{config.pk}/verify")

    assert response.status_code == 200
    assert response.json()["verified"] is False
    assert fake.closed, "the provider's HTTP client must be released either way"
    config.refresh_from_db()
    assert config.status == "invalid"
    assert config.last_verified_at is not None


def test_a_working_provider_is_marked_active(monkeypatch: Any) -> None:
    org, _project, user, _requirement = _scene()
    client = _client(user)
    config = AIProviderConfig.objects.create(
        org=org,
        provider_type="openai",
        label="Main",
        capability_models={"chat": "gpt-4o"},
        status="invalid",
    )
    monkeypatch.setattr("apps.ai.views.build_provider", lambda config: FakeAIProvider())

    response = client.post(f"/api/v1/orgs/{org.pk}/ai-providers/{config.pk}/verify")

    assert response.status_code == 200
    assert response.json()["verified"] is True
    config.refresh_from_db()
    assert config.status == "active"


def test_another_projects_trace_is_a_404(monkeypatch: Any) -> None:
    org, project, user, requirement = _scene()
    _install_provider(monkeypatch, org, FakeAIProvider(replies=[_generation_reply(1)]))
    job_id = (
        _client(user)
        .post(
            f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/analyses",
            {
                "agent": "test_generation",
                "target_type": "requirement",
                "target_id": str(requirement.pk),
            },
            format="json",
        )
        .json()["id"]
    )

    _other_org, other_project, outsider, _req = _scene()

    response = _client(outsider).get(
        f"/api/v1/orgs/{other_project.org_id}/projects/{other_project.pk}/ai/jobs/{job_id}/trace"
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# findings and recommendations
# ---------------------------------------------------------------------------
def test_an_unconfirmed_proposal_is_visible_but_creates_no_test_cases(
    monkeypatch: Any,
) -> None:
    org, project, user, requirement = _scene()
    _install_provider(monkeypatch, org, FakeAIProvider(replies=[_generation_reply(3)]))
    client = _client(user)
    client.post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/analyses",
        {
            "agent": "test_generation",
            "target_type": "requirement",
            "target_id": str(requirement.pk),
        },
        format="json",
    )

    pending = client.get(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/recommendations?status=pending"
    ).json()

    assert pending["count"] == 1
    assert TestCase.objects.filter(project=project).count() == 0


def test_confirming_over_http_creates_the_cases(monkeypatch: Any) -> None:
    org, project, user, requirement = _scene()
    _install_provider(monkeypatch, org, FakeAIProvider(replies=[_generation_reply(3)]))
    client = _client(user)
    client.post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/analyses",
        {
            "agent": "test_generation",
            "target_type": "requirement",
            "target_id": str(requirement.pk),
        },
        format="json",
    )
    recommendation = AIRecommendation.objects.get(project=project)

    response = client.post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/recommendations/{recommendation.pk}/confirm",
        {},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["status"] == RecommendationStatus.EXECUTED
    assert TestCase.objects.filter(project=project).count() == 3


def test_rejecting_over_http_creates_nothing(monkeypatch: Any) -> None:
    org, project, user, requirement = _scene()
    _install_provider(monkeypatch, org, FakeAIProvider(replies=[_generation_reply(3)]))
    client = _client(user)
    client.post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/analyses",
        {
            "agent": "test_generation",
            "target_type": "requirement",
            "target_id": str(requirement.pk),
        },
        format="json",
    )
    recommendation = AIRecommendation.objects.get(project=project)

    response = client.post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/recommendations/{recommendation.pk}/reject",
        {"reason": "already covered"},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["status"] == RecommendationStatus.REJECTED
    assert response.json()["confirmation"]["reason"] == "already covered"
    assert TestCase.objects.filter(project=project).count() == 0


def test_confirming_twice_is_a_conflict(monkeypatch: Any) -> None:
    org, project, user, requirement = _scene()
    _install_provider(monkeypatch, org, FakeAIProvider(replies=[_generation_reply(2)]))
    client = _client(user)
    client.post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/analyses",
        {
            "agent": "test_generation",
            "target_type": "requirement",
            "target_id": str(requirement.pk),
        },
        format="json",
    )
    recommendation = AIRecommendation.objects.get(project=project)
    url = (
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/recommendations/"
        f"{recommendation.pk}/confirm"
    )

    assert client.post(url, {}, format="json").status_code == 200
    second = client.post(url, {}, format="json")

    assert second.status_code == 409
    error = second.json()["error"]
    assert error["code"] == "cannot_execute"
    assert error["request_id"], "a 409 must carry the request id like every other error"
    assert TestCase.objects.filter(project=project).count() == 2


# ---------------------------------------------------------------------------
# permissions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("role", list(Role), ids=[role.value for role in Role])
def test_a_viewer_can_read_but_not_run_or_confirm(role: Role, monkeypatch: Any) -> None:
    org, project, user, requirement = _scene(role)
    _install_provider(monkeypatch, org, FakeAIProvider(replies=[_generation_reply(2)]))
    client = _client(user)

    start = client.post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/analyses",
        {
            "agent": "test_generation",
            "target_type": "requirement",
            "target_id": str(requirement.pk),
        },
        format="json",
    )
    listing = client.get(f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/findings")

    assert listing.status_code == 200
    expected = 202 if role is not Role.VIEWER else 403
    assert start.status_code == expected


@pytest.mark.parametrize("role", list(Role), ids=[role.value for role in Role])
def test_provider_credentials_are_admin_only(role: Role) -> None:
    org, _project, user, _requirement = _scene(role)

    response = _client(user).get(f"/api/v1/orgs/{org.pk}/ai-providers")

    assert response.status_code == 200 if role is Role.ADMIN else 403


def test_provider_secrets_are_write_only() -> None:
    org, _project, user, _requirement = _scene(Role.ADMIN)
    client = _client(user)

    created = client.post(
        f"/api/v1/orgs/{org.pk}/ai-providers",
        {
            "provider_type": "openai",
            "label": "Primary",
            "api_key": "sk-super-secret",
            "capability_models": {"chat": "gpt-4o"},
            "is_default": True,
        },
        format="json",
    )

    assert created.status_code == 201
    assert created.json()["has_api_key"] is True
    assert "sk-super-secret" not in created.content.decode()

    fetched = client.get(f"/api/v1/orgs/{org.pk}/ai-providers/{created.json()['id']}")
    assert "sk-super-secret" not in fetched.content.decode()


# ---------------------------------------------------------------------------
# tenant isolation
# ---------------------------------------------------------------------------
def test_another_projects_job_is_a_404() -> None:
    org, project, user, requirement = _scene()
    job_id = (
        _client(user)
        .post(
            f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/analyses",
            {
                "agent": "test_generation",
                "target_type": "requirement",
                "target_id": str(requirement.pk),
            },
            format="json",
        )
        .json()["id"]
    )

    _other_org, other_project, outsider, _req = _scene()

    response = _client(outsider).get(
        f"/api/v1/orgs/{other_project.org_id}/projects/{other_project.pk}/ai/jobs/{job_id}"
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# risk
# ---------------------------------------------------------------------------
def test_the_commit_risk_endpoint_returns_a_breakdown_that_adds_up() -> None:
    org, project, user, _requirement = _scene()
    commit = Commit.objects.get(repository__project=project, sha="abc123")

    response = _client(user).get(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/commits/{commit.pk}/risk"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["level"] in {"low", "medium", "high", "critical"}
    total = sum(row["contribution"] for row in body["breakdown"])
    assert total == pytest.approx(body["score"], abs=1e-6)
    rows = {row["signal"] for row in body["breakdown"]}
    assert "change_volume" in rows
    assert all(row["label"] and row["detail"] for row in body["breakdown"])


def test_the_module_risk_endpoint_works() -> None:
    org, project, user, _requirement = _scene()
    module = Module.objects.get(project=project, path_prefix="src/payment")

    response = _client(user).get(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/modules/{module.pk}/risk"
    )

    assert response.status_code == 200
    assert response.json()["score"] >= 0


def test_a_foreign_commit_risk_is_a_404() -> None:
    _org, project, _user, _requirement = _scene()
    commit = Commit.objects.get(repository__project=project, sha="abc123")
    _other_org, other_project, outsider, _req = _scene()

    response = _client(outsider).get(
        f"/api/v1/orgs/{other_project.org_id}/projects/{other_project.pk}/commits/"
        f"{commit.pk}/risk"
    )

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"], "a 404 must carry the request id like every other error"


def test_risk_is_visible_to_every_member() -> None:
    for role in (Role.VIEWER, Role.QA):
        org, project, user, _requirement = _scene(role)
        commit = Commit.objects.get(repository__project=project, sha="abc123")

        response = _client(user).get(
            f"/api/v1/orgs/{org.pk}/projects/{project.pk}/commits/{commit.pk}/risk"
        )

        assert response.status_code == 200


def test_findings_carry_their_agent_and_payload(monkeypatch: Any) -> None:
    org, project, user, requirement = _scene()
    _install_provider(monkeypatch, org, FakeAIProvider(replies=[_generation_reply(1)]))
    client = _client(user)
    client.post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/analyses",
        {
            "agent": "test_generation",
            "target_type": "requirement",
            "target_id": str(requirement.pk),
        },
        format="json",
    )

    body = client.get(f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/findings").json()

    assert body["count"] == 1
    finding = body["results"][0]
    assert finding["agent_code"] == "test_generation"
    assert finding["payload"]["output"]["summary"]
    assert AIFinding.objects.filter(project=project).exists()


# ---------------------------------------------------------------------------
# triage and the audit trail
# ---------------------------------------------------------------------------
def _run_one_analysis(monkeypatch: Any, org: Any, project: Any, user: Any, requirement: Any) -> Any:
    _install_provider(monkeypatch, org, FakeAIProvider(replies=[_generation_reply(1)]))
    _client(user).post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/analyses",
        {
            "agent": "test_generation",
            "target_type": "requirement",
            "target_id": str(requirement.pk),
        },
        format="json",
    )
    return AIFinding.objects.get(project=project)


def test_a_finding_can_be_triaged_and_the_change_is_audited(monkeypatch: Any) -> None:
    """A finding's content is the model's; its status is the reader's."""
    org, project, user, requirement = _scene()
    finding = _run_one_analysis(monkeypatch, org, project, user, requirement)

    response = _client(user).post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/findings/{finding.pk}/status",
        {"status": "acknowledged"},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "acknowledged"
    audit = AuditLog.objects.get(action="ai_finding.status_changed")
    assert audit.before["status"] == "new"
    assert audit.after["status"] == "acknowledged"


def test_an_unknown_finding_status_is_rejected(monkeypatch: Any) -> None:
    org, project, user, requirement = _scene()
    finding = _run_one_analysis(monkeypatch, org, project, user, requirement)

    response = _client(user).post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/findings/{finding.pk}/status",
        {"status": "done-for"},
        format="json",
    )

    assert response.status_code == 400
    finding.refresh_from_db()
    assert finding.status == "new"


def test_a_viewer_cannot_triage_a_finding(monkeypatch: Any) -> None:
    org, project, viewer, requirement = _scene(Role.VIEWER)
    # The finding must exist before anyone can be forbidden from triaging it, and only a
    # role that may run analyses can produce one.
    admin = cast(User, UserFactory())
    MembershipFactory(org=org, user=admin, role=Role.ADMIN)
    finding = _run_one_analysis(monkeypatch, org, project, admin, requirement)

    response = _client(viewer).post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/findings/{finding.pk}/status",
        {"status": "dismissed"},
        format="json",
    )

    assert response.status_code == 403
    finding.refresh_from_db()
    assert finding.status == "new"


def test_findings_order_by_severity_by_rank_not_alphabetically() -> None:
    """`severity` is a text column; alphabetical order would be meaningless."""
    org, project, user, _requirement = _scene()
    for severity in ("info", "critical", "low"):
        AIFinding.objects.create(
            project=project, agent_code="code_impact", severity=severity, title=f"{severity}!"
        )
    base = f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/findings"

    worst_first = _client(user).get(f"{base}?ordering=-severity").json()["results"]
    mildest_first = _client(user).get(f"{base}?ordering=severity").json()["results"]

    assert [row["severity"] for row in worst_first] == ["critical", "low", "info"]
    assert [row["severity"] for row in mildest_first] == ["info", "low", "critical"]


def test_the_audit_trail_is_admin_only(monkeypatch: Any) -> None:
    org, project, admin, requirement = _scene()
    finding = _run_one_analysis(monkeypatch, org, project, admin, requirement)
    _client(admin).post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/ai/findings/{finding.pk}/status",
        {"status": "converted"},
        format="json",
    )

    listing = _client(admin).get(f"/api/v1/orgs/{org.pk}/audit-logs")

    assert listing.status_code == 200
    assert listing.json()["count"] == 1
    assert listing.json()["results"][0]["action"] == "ai_finding.status_changed"

    _other_org, other_project, viewer, _req = _scene(Role.VIEWER)
    forbidden = _client(viewer).get(f"/api/v1/orgs/{other_project.org_id}/audit-logs")
    assert forbidden.status_code == 403
