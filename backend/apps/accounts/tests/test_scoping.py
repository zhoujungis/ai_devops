"""Cross-tenant isolation.

The contract these tests pin down: a resource the caller cannot see is a **404**,
never a 403. A 403 would confirm that the other tenant exists.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Organization, Project, User
from apps.accounts.roles import Role
from apps.accounts.tests.factories import (
    MembershipFactory,
    OrganizationFactory,
    ProjectFactory,
    ProjectMembershipFactory,
    UserFactory,
)
from apps.core.scoping import scoped_queryset

pytestmark = pytest.mark.django_db


def _client_for(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def home_org(db: Any) -> Organization:
    """The organization the outsider genuinely belongs to."""
    return cast(Organization, OrganizationFactory(name="Home Corp"))


@pytest.fixture
def outsider(db: Any, home_org: Organization) -> User:
    """A user who belongs to their own organization and nothing else."""
    user = cast(User, UserFactory())
    MembershipFactory(org=home_org, user=user, role=Role.ADMIN)
    return user


@pytest.fixture
def foreign_org(db: Any) -> Organization:
    org = cast(Organization, OrganizationFactory(name="Rival Corp"))
    MembershipFactory(org=org, user=UserFactory(), role=Role.ADMIN)
    return org


@pytest.fixture
def foreign_project(db: Any, foreign_org: Organization) -> Project:
    return cast(Project, ProjectFactory(org=foreign_org, name="Rival Payments"))


def test_foreign_organization_detail_is_404_not_403(
    outsider: User, foreign_org: Organization
) -> None:
    response = _client_for(outsider).get(f"/api/v1/orgs/{foreign_org.pk}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_foreign_organization_members_are_404(outsider: User, foreign_org: Organization) -> None:
    response = _client_for(outsider).get(f"/api/v1/orgs/{foreign_org.pk}/members")

    assert response.status_code == 404


def test_foreign_project_list_is_404(outsider: User, foreign_org: Organization) -> None:
    response = _client_for(outsider).get(f"/api/v1/orgs/{foreign_org.pk}/projects")

    assert response.status_code == 404


def test_foreign_project_detail_is_404(
    outsider: User, foreign_org: Organization, foreign_project: Project
) -> None:
    response = _client_for(outsider).get(
        f"/api/v1/orgs/{foreign_org.pk}/projects/{foreign_project.pk}"
    )

    assert response.status_code == 404


def test_foreign_organization_cannot_be_deleted(outsider: User, foreign_org: Organization) -> None:
    response = _client_for(outsider).delete(f"/api/v1/orgs/{foreign_org.pk}")

    assert response.status_code == 404
    assert Organization.objects.filter(pk=foreign_org.pk).exists()


def test_organization_list_contains_only_my_organizations(
    outsider: User, home_org: Organization, foreign_org: Organization
) -> None:
    response = _client_for(outsider).get("/api/v1/orgs")

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()["results"]]
    assert ids == [str(home_org.pk)]
    assert str(foreign_org.pk) not in ids


def test_organization_list_does_not_duplicate_a_multi_member_organization(db: Any) -> None:
    org = cast(Organization, OrganizationFactory())
    user = cast(User, UserFactory())
    MembershipFactory(org=org, user=user, role=Role.ADMIN)
    MembershipFactory(org=org, user=UserFactory(), role=Role.VIEWER)

    response = _client_for(user).get("/api/v1/orgs")

    assert [row["id"] for row in response.json()["results"]] == [str(org.pk)]


def test_role_insufficient_is_403_not_404(db: Any) -> None:
    """A member who lacks the role gets a 403: they can see the org, just not do this."""
    org = cast(Organization, OrganizationFactory())
    viewer = cast(User, UserFactory())
    MembershipFactory(org=org, user=viewer, role=Role.VIEWER)

    response = _client_for(viewer).patch(
        f"/api/v1/orgs/{org.pk}", {"name": "Renamed"}, format="json"
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


def test_scoped_queryset_returns_only_my_tenants(
    outsider: User, home_org: Organization, foreign_org: Organization
) -> None:
    visible = scoped_queryset(Organization, outsider)

    assert list(visible) == [home_org]
    assert foreign_org not in visible


def test_scoped_queryset_scopes_memberships(outsider: User, foreign_org: Organization) -> None:
    visible = scoped_queryset(Membership, outsider)

    assert all(membership.org_id != foreign_org.pk for membership in visible)


def test_scoped_queryset_refuses_models_without_a_scope(outsider: User) -> None:
    """A model that never declared its scope must fail loudly, not leak."""
    with pytest.raises(ImproperlyConfigured):
        scoped_queryset(cast(Any, Group), outsider)


def test_project_membership_elevates_the_organization_role(
    outsider: User, foreign_org: Organization, foreign_project: Project
) -> None:
    """Being an org viewer but a project admin is enough to administer that project."""
    assert foreign_project.role_for(outsider) is None

    ProjectMembershipFactory(project=foreign_project, user=outsider, role=Role.ADMIN)

    assert foreign_project.role_for(outsider) is Role.ADMIN
