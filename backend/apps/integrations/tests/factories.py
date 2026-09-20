"""Factories for integration and codebase tests."""

from __future__ import annotations

from typing import Any, cast

import factory
from factory.django import DjangoModelFactory

from apps.accounts.models import Project
from apps.accounts.tests.factories import OrganizationFactory
from apps.integrations.models import GitConnection, Repository


class GitConnectionFactory(DjangoModelFactory):
    class Meta:
        model = GitConnection

    org = factory.SubFactory(OrganizationFactory)
    label = factory.Sequence(lambda n: f"GitHub {n}")
    token = "ghp_test_token"
    webhook_secret = "webhook-secret"


class RepositoryFactory(DjangoModelFactory):
    class Meta:
        model = Repository

    project = factory.SubFactory(Project)
    connection = factory.SubFactory(GitConnectionFactory)
    external_id = factory.Sequence(lambda n: str(1000 + n))
    full_name = factory.Sequence(lambda n: f"acme/repo{n}")
    default_branch = "main"


def build_connection(org: Any, **kwargs: Any) -> GitConnection:
    return cast(GitConnection, GitConnectionFactory(org=org, **kwargs))


def build_repository(project: Project, **kwargs: Any) -> Repository:
    connection_kwargs = kwargs.pop("connection_kwargs", {})
    connection = build_connection(project.org, **connection_kwargs)
    return cast(Repository, RepositoryFactory(project=project, connection=connection, **kwargs))
