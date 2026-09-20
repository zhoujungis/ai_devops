"""S3 endpoints: the role matrix, tenant isolation and the explain endpoint.

The role matrix here is deliberately *not* hierarchical. The approved permission
model says QA may edit test cases but PM may not, and that PM manages releases but
developers do not — neither is expressible as "at least role X", so these tests are
what keep the explicit role sets honest.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
from apps.bugs.models import Bug, BugModuleLink, BugStatus, Severity
from apps.codebase.ingest import ingest_commit
from apps.codebase.models import Commit, Module
from apps.core.models import LinkSource
from apps.integrations.tests.factories import build_repository
from apps.integrations.tests.fakes import make_remote_commit, make_remote_file
from apps.releases.models import Release, ReleaseCommitLink
from apps.requirements.models import ModuleRequirementLink, Requirement
from apps.testing.models import TestCase, TestCaseCommitLink, TestCaseModuleLink
from services.linking import link_commit_to_requirement

pytestmark = pytest.mark.django_db

PAYMENT_FILE = "src/payment/PaymentService.java"


@dataclass
class Scene:
    org: Organization
    project: Project
    actor: User
    requirement: Requirement
    test_case: TestCase
    bug: Bug
    release: Release
    commit: Commit

    def urls(self) -> dict[str, str]:
        base = f"/api/v1/orgs/{self.org.pk}/projects/{self.project.pk}"
        return {
            "requirement_list": f"{base}/requirements",
            "requirement_detail": f"{base}/requirements/{self.requirement.pk}",
            "test_case_list": f"{base}/test-cases",
            "test_case_detail": f"{base}/test-cases/{self.test_case.pk}",
            "test_run_list": f"{base}/test-runs",
            "bug_list": f"{base}/bugs",
            "bug_detail": f"{base}/bugs/{self.bug.pk}",
            "release_list": f"{base}/releases",
            "release_detail": f"{base}/releases/{self.release.pk}",
            "commit_explain": f"{base}/commits/{self.commit.pk}/explain",
        }


def build_scene(role: Role) -> Scene:
    org = cast(Organization, OrganizationFactory())
    actor = cast(User, UserFactory())
    MembershipFactory(org=org, user=actor, role=role)
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
    module = Module.objects.get(project=project, path_prefix="src/payment")

    requirement = Requirement.objects.create(
        project=project, external_key="PAY-18", title="Retry failed payments"
    )
    link_commit_to_requirement(commit)
    ModuleRequirementLink.objects.create(
        requirement=requirement, module=module, source=LinkSource.MANUAL
    )

    test_case = TestCase.objects.create(project=project, key="TC-001", title="Payment succeeds")
    TestCaseModuleLink.objects.create(test_case=test_case, module=module)

    bug = Bug.objects.create(
        project=project, key="BUG-1023", title="Duplicate charge", severity=Severity.S1
    )
    BugModuleLink.objects.create(bug=bug, module=module)

    release = Release.objects.create(project=project, version="2026.09")
    ReleaseCommitLink.objects.create(release=release, commit=commit)

    return Scene(
        org=org,
        project=project,
        actor=actor,
        requirement=requirement,
        test_case=test_case,
        bug=bug,
        release=release,
        commit=commit,
    )


@dataclass(frozen=True)
class Case:
    name: str
    method: str
    target: str
    allowed: frozenset[Role]
    payload: Callable[[Scene], dict[str, Any]] | None = None


READ_ALL = frozenset(Role)
REQUIREMENT_WRITE = frozenset({Role.ADMIN, Role.PM, Role.DEVELOPER})
TEST_WRITE = frozenset({Role.ADMIN, Role.DEVELOPER, Role.QA})
BUG_WRITE = frozenset({Role.ADMIN, Role.PM, Role.DEVELOPER, Role.QA})
RELEASE_WRITE = frozenset({Role.ADMIN, Role.PM})

CASES: list[Case] = [
    Case("requirement_list", "get", "requirement_list", READ_ALL),
    Case(
        "requirement_create",
        "post",
        "requirement_list",
        REQUIREMENT_WRITE,
        lambda _: {"external_key": "PAY-2", "title": "New"},
    ),
    Case(
        "requirement_update",
        "patch",
        "requirement_detail",
        REQUIREMENT_WRITE,
        lambda _: {"title": "Renamed"},
    ),
    Case("requirement_delete", "delete", "requirement_detail", REQUIREMENT_WRITE, lambda _: {}),
    Case("test_case_list", "get", "test_case_list", READ_ALL),
    Case(
        "test_case_create",
        "post",
        "test_case_list",
        TEST_WRITE,
        lambda _: {"key": "TC-100", "title": "New case"},
    ),
    Case("test_case_update", "patch", "test_case_detail", TEST_WRITE, lambda _: {"title": "R"}),
    Case("test_run_list", "get", "test_run_list", READ_ALL),
    Case(
        "test_run_create",
        "post",
        "test_run_list",
        TEST_WRITE,
        lambda _: {"environment": "staging"},
    ),
    Case("bug_list", "get", "bug_list", READ_ALL),
    Case("bug_create", "post", "bug_list", BUG_WRITE, lambda _: {"key": "BUG-9", "title": "New"}),
    Case(
        "bug_update", "patch", "bug_detail", BUG_WRITE, lambda _: {"status": BugStatus.IN_PROGRESS}
    ),
    Case("release_list", "get", "release_list", READ_ALL),
    Case("release_create", "post", "release_list", RELEASE_WRITE, lambda _: {"version": "1.2.3"}),
    Case("release_update", "patch", "release_detail", RELEASE_WRITE, lambda _: {"name": "R"}),
    Case("commit_explain", "get", "commit_explain", READ_ALL),
]


@pytest.mark.parametrize("role", list(Role), ids=[role.value for role in Role])
@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_role_matrix(case: Case, role: Role) -> None:
    scene = build_scene(role)
    client = APIClient()
    client.force_authenticate(user=scene.actor)

    payload = case.payload(scene) if case.payload is not None else {}
    response = getattr(client, case.method)(scene.urls()[case.target], payload, format="json")

    if role in case.allowed:
        assert response.status_code < 400, (
            f"{role.value} should be allowed to {case.method} {case.name}, "
            f"got {response.status_code}: {response.content[:300]!r}"
        )
    else:
        assert response.status_code == 403, (
            f"{role.value} must be forbidden from {case.method} {case.name}, "
            f"got {response.status_code}: {response.content[:300]!r}"
        )


def test_qa_may_edit_test_cases_but_pm_may_not() -> None:
    """The rule a simple "minimum role" could not express, stated on its own."""
    qa_scene = build_scene(Role.QA)
    pm_scene = build_scene(Role.PM)

    qa = APIClient()
    qa.force_authenticate(user=qa_scene.actor)
    pm = APIClient()
    pm.force_authenticate(user=pm_scene.actor)

    qa_response = qa.patch(qa_scene.urls()["test_case_detail"], {"title": "Renamed"}, format="json")
    pm_response = pm.patch(pm_scene.urls()["test_case_detail"], {"title": "Renamed"}, format="json")

    assert qa_response.status_code == 200
    assert pm_response.status_code == 403


# ---------------------------------------------------------------------------
def test_the_explain_endpoint_returns_the_whole_chain() -> None:
    scene = build_scene(Role.VIEWER)
    client = APIClient()
    client.force_authenticate(user=scene.actor)

    response = client.get(scene.urls()["commit_explain"])

    assert response.status_code == 200
    body = response.json()
    assert body["commit"]["sha"] == "abc123"
    assert [row["module"]["path_prefix"] for row in body["modules"]] == ["src/payment"]
    assert body["requirement"]["external_key"] == "PAY-18"
    assert body["requirement_source"] == "commit"
    assert [row["test_case"]["key"] for row in body["regression_candidates"]] == ["TC-001"]
    assert [row["bug"]["key"] for row in body["historical_bugs"]] == ["BUG-1023"]
    assert [row["version"] for row in body["releases"]] == ["2026.09"]


def test_every_explained_candidate_states_its_reason() -> None:
    scene = build_scene(Role.VIEWER)
    client = APIClient()
    client.force_authenticate(user=scene.actor)

    body = client.get(scene.urls()["commit_explain"]).json()

    candidate = body["regression_candidates"][0]
    assert candidate["reasons"], "an unexplained candidate is not auditable"
    assert candidate["score"] > 0


def test_the_explain_endpoint_reports_data_gaps() -> None:
    org = cast(Organization, OrganizationFactory())
    actor = cast(User, UserFactory())
    MembershipFactory(org=org, user=actor, role=Role.ADMIN)
    project = cast(Project, ProjectFactory(org=org))
    repository = build_repository(project)
    commit = ingest_commit(
        repository,
        make_remote_commit("lonely", minutes_ago=5, files=[make_remote_file(PAYMENT_FILE)]),
    )
    client = APIClient()
    client.force_authenticate(user=actor)

    body = client.get(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/commits/{commit.pk}/explain"
    ).json()

    assert body["requirement"] is None
    assert any("No test cases" in gap for gap in body["data_gaps"])


def test_a_foreign_project_commit_explain_is_a_404() -> None:
    scene = build_scene(Role.ADMIN)
    outsider_org = cast(Organization, OrganizationFactory())
    outsider = cast(User, UserFactory())
    MembershipFactory(org=outsider_org, user=outsider, role=Role.ADMIN)

    client = APIClient()
    client.force_authenticate(user=outsider)

    response = client.get(scene.urls()["commit_explain"])

    assert response.status_code == 404


def test_requirements_can_be_written_with_their_items() -> None:
    scene = build_scene(Role.DEVELOPER)
    client = APIClient()
    client.force_authenticate(user=scene.actor)

    created = client.post(
        scene.urls()["requirement_list"],
        {
            "external_key": "pay-42",
            "title": "Refunds",
            "items": [
                {"seq": 1, "type": "scenario", "text": "A refund is issued."},
                {"seq": 2, "type": "edge_case", "text": "Refund exceeds the capture."},
            ],
        },
        format="json",
    )

    assert created.status_code == 201
    body = created.json()
    assert body["external_key"] == "PAY-42", "keys are normalised to upper case"
    assert [item["text"] for item in body["items"]] == [
        "A refund is issued.",
        "Refund exceeds the capture.",
    ]

    replaced = client.patch(
        scene.urls()["requirement_list"] + f"/{body['id']}",
        {"items": [{"seq": 1, "type": "acceptance", "text": "Only one refund."}]},
        format="json",
    )

    assert [item["text"] for item in replaced.json()["items"]] == ["Only one refund."]


def test_a_duplicate_requirement_key_is_rejected() -> None:
    scene = build_scene(Role.DEVELOPER)
    client = APIClient()
    client.force_authenticate(user=scene.actor)

    response = client.post(
        scene.urls()["requirement_list"],
        {"external_key": "PAY-18", "title": "Same key"},
        format="json",
    )

    assert response.status_code == 400
    assert "external_key" in response.json()["error"]["details"]


@pytest.mark.parametrize(
    ("target", "payload", "field"),
    [
        ("test_case_list", {"key": "TC-001", "title": "Clash"}, "key"),
        ("bug_list", {"key": "BUG-1023", "title": "Clash"}, "key"),
        ("release_list", {"version": "2026.09"}, "version"),
    ],
)
def test_duplicate_per_project_keys_are_rejected_not_500(
    target: str, payload: dict[str, Any], field: str
) -> None:
    """Each of these would otherwise hit the database constraint and return a 500.

    The owning project is not in the request body, so DRF cannot derive the
    uniqueness validator; the explicit check in ProjectScopedUniqueMixin is what
    turns the clash into a field error.
    """
    scene = build_scene(Role.ADMIN)
    client = APIClient()
    client.force_authenticate(user=scene.actor)

    response = client.post(scene.urls()[target], payload, format="json")

    assert response.status_code == 400, response.content[:300]
    assert field in response.json()["error"]["details"]


def test_test_cases_expose_the_modules_they_cover() -> None:
    scene = build_scene(Role.VIEWER)
    client = APIClient()
    client.force_authenticate(user=scene.actor)

    body = client.get(scene.urls()["test_case_list"]).json()

    assert body["results"][0]["module_paths"] == ["src/payment"]


def test_bugs_expose_their_module_links() -> None:
    scene = build_scene(Role.VIEWER)
    client = APIClient()
    client.force_authenticate(user=scene.actor)

    body = client.get(scene.urls()["bug_list"]).json()

    assert body["results"][0]["module_links"][0]["module"]["path_prefix"] == "src/payment"


def test_a_test_case_that_verified_a_related_commit_gains_a_reason() -> None:
    scene = build_scene(Role.VIEWER)
    earlier = ingest_commit(
        scene.commit.repository,
        make_remote_commit("earlier", minutes_ago=60, files=[make_remote_file(PAYMENT_FILE)]),
    )
    TestCaseCommitLink.objects.create(
        test_case=scene.test_case, commit=earlier, source=LinkSource.MANUAL
    )

    client = APIClient()
    client.force_authenticate(user=scene.actor)
    body = client.get(scene.urls()["commit_explain"]).json()

    candidate = body["regression_candidates"][0]
    assert any("verified a commit" in reason for reason in candidate["reasons"])
