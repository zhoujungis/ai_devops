"""Integration endpoints: role gates, tenant boundaries and secret handling."""

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
from apps.codebase.ingest import ingest_commit
from apps.integrations.models import Repository
from apps.integrations.tests.factories import build_connection, build_repository
from apps.integrations.tests.fakes import FakeGitProvider, make_remote_commit, make_remote_file

pytestmark = pytest.mark.django_db


class _QueuedTask:
    """Stands in for the Celery task so the sync endpoint does not reach the network."""

    id = "task-id"

    def delay(self, *args: Any, **kwargs: Any) -> _QueuedTask:
        return self


def _scene(role: Role) -> tuple[Organization, Project, User]:
    org = cast(Organization, OrganizationFactory())
    actor = cast(User, UserFactory())
    MembershipFactory(org=org, user=actor, role=role)
    project = cast(Project, ProjectFactory(org=org))
    return org, project, actor


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.parametrize("role", list(Role), ids=[role.value for role in Role])
def test_git_connections_are_admin_only(role: Role) -> None:
    org, _project, actor = _scene(role)

    response = _client(actor).get(f"/api/v1/orgs/{org.pk}/git-connections")

    assert response.status_code == (200 if role is Role.ADMIN else 403)


@pytest.mark.parametrize("role", list(Role), ids=[role.value for role in Role])
def test_repositories_are_visible_to_every_member(role: Role) -> None:
    org, project, actor = _scene(role)
    build_repository(project)

    response = _client(actor).get(f"/api/v1/orgs/{org.pk}/projects/{project.pk}/repositories")

    assert response.status_code == 200
    assert len(response.json()["results"]) == 1


@pytest.mark.parametrize("role", list(Role), ids=[role.value for role in Role])
def test_syncing_requires_the_developer_role(role: Role, monkeypatch: Any) -> None:
    org, project, actor = _scene(role)
    repository = build_repository(project)
    monkeypatch.setattr("apps.integrations.views.sync_repository_task", _QueuedTask())

    response = _client(actor).post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/repositories/{repository.pk}/sync"
    )

    assert response.status_code == (202 if role.at_least(Role.DEVELOPER) else 403)


def test_a_foreign_organization_is_a_404_not_a_403() -> None:
    _org, project, _actor = _scene(Role.ADMIN)
    outsider = cast(User, UserFactory())

    response = _client(outsider).get(f"/api/v1/orgs/{project.org_id}/git-connections")

    assert response.status_code == 404


def test_secrets_are_write_only() -> None:
    org, _project, actor = _scene(Role.ADMIN)
    client = _client(actor)

    created = client.post(
        f"/api/v1/orgs/{org.pk}/git-connections",
        {
            "label": "GitHub",
            "provider": "github",
            "token": "ghp_super_secret_value",
            "webhook_secret": "whsec_value",
        },
        format="json",
    )

    assert created.status_code == 201
    body = created.json()
    assert body["has_token"] is True
    assert "token" not in body
    assert "webhook_secret" not in body

    fetched = client.get(f"/api/v1/orgs/{org.pk}/git-connections/{body['id']}")

    assert "ghp_super_secret_value" not in fetched.content.decode()
    assert "whsec_value" not in fetched.content.decode()


def test_the_webhook_url_is_returned() -> None:
    org, _project, actor = _scene(Role.ADMIN)

    response = _client(actor).post(
        f"/api/v1/orgs/{org.pk}/git-connections", {"label": "GH"}, format="json"
    )

    assert response.status_code == 201
    assert response.json()["webhook_url"].endswith(
        f"/api/v1/webhooks/github/{response.json()['id']}"
    )


def test_connecting_a_repository_resolves_metadata_from_the_provider(
    monkeypatch: Any,
) -> None:
    org, project, actor = _scene(Role.PM)
    connection = build_connection(org, label="GitHub")
    monkeypatch.setattr(
        "apps.integrations.services.provider_for", lambda *args, **kwargs: FakeGitProvider()
    )

    response = _client(actor).post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/repositories",
        {"connection": str(connection.pk), "full_name": "acme/payments"},
        format="json",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["full_name"] == "acme/payments"
    assert body["external_id"] == "42"
    assert body["default_branch"] == "main"


def test_a_connection_from_another_organization_is_rejected(monkeypatch: Any) -> None:
    org, project, actor = _scene(Role.PM)
    _other_org, other_project, _other_actor = _scene(Role.ADMIN)
    foreign_connection = build_connection(other_project.org, label="Someone else")
    monkeypatch.setattr(
        "apps.integrations.services.provider_for", lambda *args, **kwargs: FakeGitProvider()
    )

    response = _client(actor).post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/repositories",
        {"connection": str(foreign_connection.pk), "full_name": "acme/payments"},
        format="json",
    )

    assert response.status_code == 400
    assert "connection" in response.json()["error"]["details"]


def test_full_name_must_look_like_owner_slash_repository(monkeypatch: Any) -> None:
    org, project, actor = _scene(Role.PM)
    connection = build_connection(org)
    monkeypatch.setattr(
        "apps.integrations.services.provider_for", lambda *args, **kwargs: FakeGitProvider()
    )

    response = _client(actor).post(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/repositories",
        {"connection": str(connection.pk), "full_name": "not-a-full-name"},
        format="json",
    )

    assert response.status_code == 400


def test_deleting_a_connection_in_use_is_a_conflict() -> None:
    org, project, actor = _scene(Role.ADMIN)
    repository = build_repository(project)

    response = _client(actor).delete(
        f"/api/v1/orgs/{org.pk}/git-connections/{repository.connection_id}"
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "connection_in_use"


def _ingested_repository(project: Project) -> Repository:
    repository = build_repository(project)
    ingest_commit(
        repository,
        make_remote_commit(
            "abc123",
            minutes_ago=5,
            files=[
                make_remote_file("src/payment/PaymentService.java"),
                make_remote_file("src/order/OrderService.java"),
            ],
        ),
    )
    return repository


def test_commits_expose_the_modules_they_touched() -> None:
    org, project, actor = _scene(Role.VIEWER)
    _ingested_repository(project)

    response = _client(actor).get(f"/api/v1/orgs/{org.pk}/projects/{project.pk}/commits")

    assert response.status_code == 200
    commit = response.json()["results"][0]
    assert commit["sha"] == "abc123"
    assert commit["short_sha"] == "abc123"
    prefixes = {impact["module"]["path_prefix"] for impact in commit["module_impacts"]}
    assert prefixes == {"src/payment", "src/order"}


def test_the_modules_endpoint_lists_resolved_modules() -> None:
    org, project, actor = _scene(Role.VIEWER)
    _ingested_repository(project)

    response = _client(actor).get(f"/api/v1/orgs/{org.pk}/projects/{project.pk}/modules")

    assert response.status_code == 200
    names = {row["path_prefix"] for row in response.json()["results"]}
    assert names == {"src/payment", "src/order"}


def test_commit_detail_includes_the_changed_files() -> None:
    org, project, actor = _scene(Role.VIEWER)
    _ingested_repository(project)
    commit_id = (
        _client(actor)
        .get(f"/api/v1/orgs/{org.pk}/projects/{project.pk}/commits")
        .json()["results"][0]["id"]
    )

    response = _client(actor).get(
        f"/api/v1/orgs/{org.pk}/projects/{project.pk}/commits/{commit_id}"
    )

    assert response.status_code == 200
    paths = {changed["path"] for changed in response.json()["files"]}
    assert paths == {"src/payment/PaymentService.java", "src/order/OrderService.java"}


def test_commits_of_another_project_are_not_visible() -> None:
    _org, project, actor = _scene(Role.VIEWER)
    _ingested_repository(project)
    _other_org, other_project, _other_actor = _scene(Role.ADMIN)

    response = _client(actor).get(
        f"/api/v1/orgs/{other_project.org_id}/projects/{other_project.pk}/commits"
    )

    assert response.status_code == 404
