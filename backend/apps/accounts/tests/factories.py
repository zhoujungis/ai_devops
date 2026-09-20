"""Factories and scene helpers for accounts tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import factory
from factory.django import DjangoModelFactory

from apps.accounts.models import Membership, Organization, Project, ProjectMembership, User
from apps.accounts.roles import Role

DEFAULT_PASSWORD = "test-pass-1234"


class UserFactory(DjangoModelFactory):
    class Meta:
        model = User

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    display_name = factory.Faker("name")

    @classmethod
    def _create(cls, model_class: Any, *args: Any, **kwargs: Any) -> Any:
        """Create through the manager so the password is hashed properly."""
        password = kwargs.pop("password", DEFAULT_PASSWORD)
        extra = dict(kwargs)
        return model_class.objects.create_user(password=password, **extra)


class OrganizationFactory(DjangoModelFactory):
    class Meta:
        model = Organization

    name = factory.Sequence(lambda n: f"Organization {n}")


class MembershipFactory(DjangoModelFactory):
    class Meta:
        model = Membership

    org = factory.SubFactory(OrganizationFactory)
    user = factory.SubFactory(UserFactory)
    role = Role.VIEWER


class ProjectFactory(DjangoModelFactory):
    class Meta:
        model = Project

    org = factory.SubFactory(OrganizationFactory)
    name = factory.Sequence(lambda n: f"Project {n}")


class ProjectMembershipFactory(DjangoModelFactory):
    class Meta:
        model = ProjectMembership

    project = factory.SubFactory(ProjectFactory)
    user = factory.SubFactory(UserFactory)
    role = Role.VIEWER


@dataclass
class Scene:
    """One organization, one project, and the people a role matrix needs."""

    org: Organization
    project: Project
    actor: User
    target: User
    outsider: User

    def urls(self) -> dict[str, str]:
        base = f"/api/v1/orgs/{self.org.pk}"
        project = f"{base}/projects/{self.project.pk}"
        return {
            "org": base,
            "org_members": f"{base}/members",
            "org_member": f"{base}/members/{self.target.pk}",
            "projects": f"{base}/projects",
            "project": project,
            "project_members": f"{project}/members",
            "project_member": f"{project}/members/{self.target.pk}",
        }


def build_scene(actor_role: Role) -> Scene:
    """Build a fresh organization with ``actor_role`` for the acting user.

    ``target`` is an ordinary member the actor may administer; ``outsider`` is not
    a member of the organization at all.
    """
    org = cast(Organization, OrganizationFactory())
    actor = cast(User, UserFactory())
    target = cast(User, UserFactory())
    outsider = cast(User, UserFactory())

    MembershipFactory(org=org, user=actor, role=actor_role)
    MembershipFactory(org=org, user=target, role=Role.VIEWER)

    project = cast(Project, ProjectFactory(org=org, name="Payment Platform"))
    ProjectMembershipFactory(project=project, user=target, role=Role.VIEWER)

    return Scene(org=org, project=project, actor=actor, target=target, outsider=outsider)
