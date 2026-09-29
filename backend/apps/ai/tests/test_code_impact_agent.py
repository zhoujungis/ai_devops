"""The Code Impact agent, end to end.

This is the S5 acceptance test. It builds the correlation graph by hand, runs the
agent through the real job machinery with a scripted provider, and asserts the
things the product actually promises:

* the output validates against the prompt's schema;
* the risk score on the result is the **engine's**, and its breakdown adds up;
* regression candidates come from the data, each with a stated reason;
* a finding is persisted, with only evidence that resolved;
* re-running the same analysis updates rather than duplicates.
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
from apps.ai.agents.code_impact import CodeImpactAgent
from apps.ai.models import AIFinding, AIProviderConfig
from apps.ai.providers.registry import ResolvedModel
from apps.ai.tasks import create_job, run_analysis_job
from apps.ai.tests.fakes import FakeAIProvider
from apps.bugs.models import Bug, BugModuleLink, Severity
from apps.codebase.ingest import ingest_commit
from apps.codebase.models import Commit, Module
from apps.core.models import LinkSource
from apps.integrations.tests.factories import build_repository
from apps.integrations.tests.fakes import make_remote_commit, make_remote_file
from apps.requirements.models import ModuleRequirementLink, Requirement
from apps.risk.engine import RiskEngine
from apps.testing.models import TestCase, TestCaseModuleLink
from services.linking import link_commit_to_requirement

pytestmark = pytest.mark.django_db

PAYMENT_FILE = "src/payment/PaymentService.java"

VALID_REPLY: dict[str, Any] = {
    "summary": "Payment retry path changed.",
    "confidence": 0.8,
    "facts": ["The commit touches the payment module."],
    "evidence": [],
    "hypotheses": ["Retry timing may interact with the duplicate-charge guard."],
    "how_to_verify": ["Replay a timed-out capture and watch for a second charge."],
    "data_gaps": [],
    "changed_modules": [{"module": "src/payment", "impact": 4, "reason": "core payment logic"}],
    "affected_features": [{"feature": "支付", "confidence": 0.8, "path": "module->requirement"}],
    "potential_impacts": ["payment retried twice"],
    "recommended_regression_tests": [
        {"key": "TC-001", "title": "Payment succeeds", "score": 0.8, "reason": "covers src/payment"}
    ],
    "suspected_bug_patterns": [{"pattern": "duplicate charge", "historical_bug": "BUG-1023"}],
    "risk_explanation": "The engine's score is driven by a recent defect in this module.",
}


def _scene() -> tuple[Project, Commit]:
    org = cast(Organization, OrganizationFactory())
    MembershipFactory(org=org, user=UserFactory(), role="admin")
    project = cast(Project, ProjectFactory(org=org))
    repository = build_repository(project)

    commit = ingest_commit(
        repository,
        make_remote_commit(
            "abc123",
            minutes_ago=5,
            message="PAY-18 retry payment",
            files=[make_remote_file(PAYMENT_FILE)],
        ),
    )
    requirement = Requirement.objects.create(
        project=project, external_key="PAY-18", title="Retry failed payments"
    )
    link_commit_to_requirement(commit)

    module = Module.objects.get(project=project, path_prefix="src/payment")
    ModuleRequirementLink.objects.create(
        requirement=requirement, module=module, source=LinkSource.MANUAL
    )

    test_case = TestCase.objects.create(project=project, key="TC-001", title="Payment succeeds")
    TestCaseModuleLink.objects.create(test_case=test_case, module=module)

    bug = Bug.objects.create(
        project=project, key="BUG-1023", title="Duplicate charge", severity=Severity.S1
    )
    BugModuleLink.objects.create(bug=bug, module=module)

    return project, commit


def _install_provider(monkeypatch: Any, project: Project, provider: FakeAIProvider) -> None:
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


def _reply(**overrides: Any) -> str:
    return json.dumps({**VALID_REPLY, **overrides})


# ---------------------------------------------------------------------------
def test_the_agent_produces_a_validated_analysis(monkeypatch: Any) -> None:
    project, commit = _scene()
    provider = FakeAIProvider(replies=[_reply()])
    _install_provider(monkeypatch, project, provider)
    job, _ = create_job(
        project=project, agent_code="code_impact", target_type="commit", target_id=str(commit.pk)
    )

    outcome = run_analysis_job(str(job.pk))

    assert outcome["status"] == "succeeded", job.error
    assert job.error == ""


def test_the_target_may_be_a_sha_rather_than_a_pk(monkeypatch: Any) -> None:
    """Both shapes are valid targets, and the pk lookup must not throw on a sha."""
    project, commit = _scene()
    _install_provider(monkeypatch, project, FakeAIProvider(replies=[_reply()]))
    job, _ = create_job(
        project=project, agent_code="code_impact", target_type="commit", target_id=commit.sha
    )

    outcome = run_analysis_job(str(job.pk))

    assert outcome["status"] == "succeeded", job.error


def test_the_recorded_score_is_the_engines_and_its_breakdown_adds_up(monkeypatch: Any) -> None:
    project, commit = _scene()
    provider = FakeAIProvider(replies=[_reply()])
    _install_provider(monkeypatch, project, provider)
    job, _ = create_job(
        project=project, agent_code="code_impact", target_type="commit", target_id=str(commit.pk)
    )

    run_analysis_job(str(job.pk))
    job.refresh_from_db()

    expected = RiskEngine().assess_commit(commit)
    assert job.result["risk_score"] == pytest.approx(expected.score)
    assert job.result["risk_level"] == expected.level
    assert sum(row["contribution"] for row in job.result["risk_breakdown"]) == pytest.approx(
        job.result["risk_score"], abs=1e-6
    )


def test_the_model_cannot_move_the_score(monkeypatch: Any) -> None:
    """Even a model that claims a different score cannot change the recorded one."""
    project, commit = _scene()
    reply = _reply()
    provider = FakeAIProvider(replies=[reply])
    _install_provider(monkeypatch, project, provider)
    job, _ = create_job(
        project=project, agent_code="code_impact", target_type="commit", target_id=str(commit.pk)
    )

    run_analysis_job(str(job.pk))
    job.refresh_from_db()

    assert job.result["risk_score"] == pytest.approx(RiskEngine().assess_commit(commit).score)
    # And the schema has no field the model could have answered a score with.
    assert "risk_score" not in job.result["analysis"]
    assert "risk_level" not in job.result["analysis"]


def test_regression_candidates_come_from_the_data_with_reasons(monkeypatch: Any) -> None:
    project, commit = _scene()
    provider = FakeAIProvider(replies=[_reply()])
    _install_provider(monkeypatch, project, provider)
    job, _ = create_job(
        project=project, agent_code="code_impact", target_type="commit", target_id=str(commit.pk)
    )

    run_analysis_job(str(job.pk))
    job.refresh_from_db()

    candidates = job.result["regression_candidates"]
    assert [row["key"] for row in candidates] == ["TC-001"]
    assert candidates[0]["reasons"], "a candidate without a reason is not auditable"
    assert candidates[0]["score"] > 0


def test_a_finding_is_recorded_with_the_evidence_that_resolved(monkeypatch: Any) -> None:
    project, commit = _scene()
    module = Module.objects.get(project=project, path_prefix="src/payment")
    reply = _reply(
        evidence=[
            {"kind": "module", "ref_id": str(module.pk), "note": "touched"},
            {"kind": "commit", "ref_id": str(commit.pk), "note": "the change"},
            # Neither of these exists in this project.
            {"kind": "bug", "ref_id": "11111111-1111-1111-1111-111111111111"},
            {"kind": "log", "ref_id": "whatever"},
        ]
    )
    provider = FakeAIProvider(replies=[reply])
    _install_provider(monkeypatch, project, provider)
    job, _ = create_job(
        project=project, agent_code="code_impact", target_type="commit", target_id=str(commit.pk)
    )

    run_analysis_job(str(job.pk))

    finding = AIFinding.objects.get(project=project)
    assert {item["kind"] for item in finding.evidence} == {"module", "commit"}
    assert len(finding.payload["dropped_evidence"]) == 2
    assert finding.status == "new"
    assert finding.agent_code == "code_impact"


def test_the_finding_severity_follows_the_engine_level(monkeypatch: Any) -> None:
    project, commit = _scene()
    provider = FakeAIProvider(replies=[_reply()])
    _install_provider(monkeypatch, project, provider)
    job, _ = create_job(
        project=project, agent_code="code_impact", target_type="commit", target_id=str(commit.pk)
    )

    run_analysis_job(str(job.pk))

    level = RiskEngine().assess_commit(commit).level
    assert AIFinding.objects.get(project=project).severity == level


def test_re_running_the_same_analysis_updates_rather_than_duplicates(monkeypatch: Any) -> None:
    project, commit = _scene()
    provider = FakeAIProvider(replies=[_reply(), _reply()])
    _install_provider(monkeypatch, project, provider)

    for _ in range(2):
        job, _ = create_job(
            project=project,
            agent_code="code_impact",
            target_type="commit",
            target_id=str(commit.pk),
        )
        run_analysis_job(str(job.pk))

    assert AIFinding.objects.filter(project=project).count() == 1


def test_the_run_is_traced_with_prompt_version_and_tokens(monkeypatch: Any) -> None:
    project, commit = _scene()
    provider = FakeAIProvider(replies=[_reply()])
    _install_provider(monkeypatch, project, provider)
    job, _ = create_job(
        project=project, agent_code="code_impact", target_type="commit", target_id=str(commit.pk)
    )

    run_analysis_job(str(job.pk))

    run = job.runs.get()
    assert run.prompt_id == "code_impact"
    assert run.prompt_version == 1
    assert run.model == "gpt-4o"
    assert run.input_tokens == 100
    assert run.output_tokens == 20
    assert len(run.input_digest) == 64
    assert "Deterministic facts" in run.input_text
    assert run.status == "succeeded"


def test_an_invalid_reply_is_repaired_and_still_succeeds(monkeypatch: Any) -> None:
    project, commit = _scene()
    provider = FakeAIProvider(replies=["not json", _reply()])
    _install_provider(monkeypatch, project, provider)
    job, _ = create_job(
        project=project, agent_code="code_impact", target_type="commit", target_id=str(commit.pk)
    )

    outcome = run_analysis_job(str(job.pk))

    assert outcome["status"] == "succeeded"
    assert len(provider.calls) == 2


def test_a_target_outside_the_project_fails_the_job(monkeypatch: Any) -> None:
    project, _commit = _scene()
    provider = FakeAIProvider(replies=[_reply()])
    _install_provider(monkeypatch, project, provider)
    job, _ = create_job(
        project=project,
        agent_code="code_impact",
        target_type="commit",
        target_id="00000000-0000-0000-0000-000000000000",
    )

    outcome = run_analysis_job(str(job.pk))

    assert outcome["status"] == "failed"
    job.refresh_from_db()
    assert "not in project" in job.error


def test_the_agent_is_registered_under_its_code() -> None:
    from apps.ai.agents.base import agent_for

    assert isinstance(agent_for("code_impact"), CodeImpactAgent)


def test_the_provider_receives_the_engine_score_as_a_fact(monkeypatch: Any) -> None:
    """The model is told the score, not asked for one."""
    project, commit = _scene()
    provider = FakeAIProvider(replies=[_reply()])
    _install_provider(monkeypatch, project, provider)
    job, _ = create_job(
        project=project, agent_code="code_impact", target_type="commit", target_id=str(commit.pk)
    )

    run_analysis_job(str(job.pk))

    prompt = provider.calls[0][-1].content
    assert "risk_already_computed_by_the_engine" in prompt, "the score must be stated as a fact"
    assert '"breakdown"' in prompt, "the model must be given the engine's reasoning"
    assert '"score"' in prompt
