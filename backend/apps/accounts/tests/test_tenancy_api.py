"""Tenancy happy paths and membership invariants."""

from __future__ import annotations

from typing import Any, cast

import pytest
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


@pytest.fixture
def owner(db: Any) -> User:
    """A user who administers exactly one organization."""
    user = cast(User, UserFactory())
    MembershipFactory(org=OrganizationFactory(name="Acme"), user=user, role=Role.ADMIN)
    return user


@pytest.fixture
def owner_client(owner: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=owner)
    return client


def test_creating_an_organization_makes_the_creator_admin(
    auth_client: APIClient, user: User
) -> None:
    response = auth_client.post("/api/v1/orgs", {"name": "Globex"}, format="json")

    assert response.status_code == 201
    org = Organization.objects.get(pk=response.json()["id"])
    assert org.slug == "globex"
    assert org.role_for(user) is Role.ADMIN
    assert response.json()["role"] == Role.ADMIN


def test_duplicate_organization_names_get_distinct_slugs(auth_client: APIClient) -> None:
    first = auth_client.post("/api/v1/orgs", {"name": "Initech"}, format="json").json()
    second = auth_client.post("/api/v1/orgs", {"name": "Initech"}, format="json").json()

    assert first["slug"] == "initech"
    assert second["slug"] == "initech-2"


def test_project_creation_ignores_an_org_supplied_in_the_body(
    owner_client: APIClient, owner: User
) -> None:
    """The tenant comes from the URL; a body field must never move the row."""
    mine = scoped_queryset(Organization, owner).get()
    someone_else = cast(Organization, OrganizationFactory(name="Rival"))

    response = owner_client.post(
        f"/api/v1/orgs/{mine.pk}/projects",
        {"name": "Checkout", "org": str(someone_else.pk)},
        format="json",
    )

    assert response.status_code == 201
    project = Project.objects.get(pk=response.json()["id"])
    assert project.org_id == mine.pk
    assert project.org_id != someone_else.pk


def test_adding_a_member_by_email(owner_client: APIClient, owner: User) -> None:
    org = scoped_queryset(Organization, owner).get()
    newcomer = cast(User, UserFactory(email="newcomer@example.com"))

    response = owner_client.post(
        f"/api/v1/orgs/{org.pk}/members",
        {"user_email": "newcomer@example.com", "role": Role.DEVELOPER},
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["role"] == Role.DEVELOPER
    assert Membership.objects.filter(org=org, user=newcomer).exists()


def test_adding_an_unknown_email_is_rejected(owner_client: APIClient, owner: User) -> None:
    org = scoped_queryset(Organization, owner).get()

    response = owner_client.post(
        f"/api/v1/orgs/{org.pk}/members",
        {"user_email": "nobody@example.com", "role": Role.QA},
        format="json",
    )

    assert response.status_code == 400
    assert "user_email" in response.json()["error"]["details"]


def test_adding_the_same_member_twice_is_rejected(owner_client: APIClient, owner: User) -> None:
    org = scoped_queryset(Organization, owner).get()
    UserFactory(email="twice@example.com")

    first = owner_client.post(
        f"/api/v1/orgs/{org.pk}/members",
        {"user_email": "twice@example.com", "role": Role.QA},
        format="json",
    )
    second = owner_client.post(
        f"/api/v1/orgs/{org.pk}/members",
        {"user_email": "twice@example.com", "role": Role.QA},
        format="json",
    )

    assert first.status_code == 201
    assert second.status_code == 400


def test_the_last_admin_cannot_be_demoted(owner_client: APIClient, owner: User) -> None:
    org = scoped_queryset(Organization, owner).get()

    response = owner_client.patch(
        f"/api/v1/orgs/{org.pk}/members/{owner.pk}",
        {"role": Role.VIEWER},
        format="json",
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "last_admin"
    assert Membership.objects.get(org=org, user=owner).role == Role.ADMIN


def test_the_last_admin_cannot_be_removed(owner_client: APIClient, owner: User) -> None:
    org = scoped_queryset(Organization, owner).get()

    response = owner_client.delete(f"/api/v1/orgs/{org.pk}/members/{owner.pk}")

    assert response.status_code == 409
    assert Membership.objects.filter(org=org, user=owner).exists()


def test_an_admin_can_be_demoted_once_another_admin_exists(
    owner_client: APIClient, owner: User
) -> None:
    org = scoped_queryset(Organization, owner).get()
    MembershipFactory(org=org, user=UserFactory(), role=Role.ADMIN)

    response = owner_client.patch(
        f"/api/v1/orgs/{org.pk}/members/{owner.pk}",
        {"role": Role.PM},
        format="json",
    )

    assert response.status_code == 200
    assert Membership.objects.get(org=org, user=owner).role == Role.PM


def test_project_membership_grants_access_to_a_single_project(db: Any) -> None:
    """A direct project grant reaches that project without organization membership."""
    project = cast(Project, ProjectFactory(name="Contract Work"))
    contractor = cast(User, UserFactory())
    ProjectMembershipFactory(project=project, user=contractor, role=Role.QA)

    visible = scoped_queryset(Project, contractor)

    assert list(visible) == [project]
    assert project.role_for(contractor) is Role.QA


def test_organization_membership_reaches_every_project_in_it(db: Any) -> None:
    org = cast(Organization, OrganizationFactory())
    user = cast(User, UserFactory())
    MembershipFactory(org=org, user=user, role=Role.VIEWER)
    first = cast(Project, ProjectFactory(org=org))
    second = cast(Project, ProjectFactory(org=org))

    assert set(scoped_queryset(Project, user)) == {first, second}
