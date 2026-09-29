"""Centrality is derived from the commits, never entered by hand."""

from __future__ import annotations

from typing import Any, cast

import pytest

from apps.accounts.models import Project
from apps.accounts.tests.factories import OrganizationFactory, ProjectFactory
from apps.codebase.centrality import recompute_centrality
from apps.codebase.ingest import ingest_commit
from apps.codebase.models import Module
from apps.integrations.models import Repository
from apps.integrations.tests.factories import build_repository
from apps.integrations.tests.fakes import make_remote_commit, make_remote_file

pytestmark = pytest.mark.django_db


@pytest.fixture
def repository(db: Any) -> Repository:
    return build_repository(cast(Project, ProjectFactory(org=OrganizationFactory())))


def test_centrality_is_the_share_of_commits_touching_a_module(repository: Repository) -> None:
    ingest_commit(
        repository,
        make_remote_commit("c1", minutes_ago=3, files=[make_remote_file("src/alpha/a.py")]),
    )
    ingest_commit(
        repository,
        make_remote_commit("c2", minutes_ago=2, files=[make_remote_file("src/alpha/b.py")]),
    )
    ingest_commit(
        repository,
        make_remote_commit("c3", minutes_ago=1, files=[make_remote_file("src/beta/c.py")]),
    )

    updated = recompute_centrality(repository.project)

    assert updated == 2
    alpha = Module.objects.get(project=repository.project, path_prefix="src/alpha")
    beta = Module.objects.get(project=repository.project, path_prefix="src/beta")
    # The busiest module is the reference point, so it scores 1.0 — which is also the
    # scale the risk engine saturates at.
    assert alpha.centrality_score == 1.0
    assert beta.centrality_score == 0.5


def test_centrality_is_zero_when_nothing_has_been_synced(repository: Repository) -> None:
    assert recompute_centrality(repository.project) == 0


def test_a_sync_recomputes_centrality(repository: Repository) -> None:
    """The signal must not need a second command to become true."""
    from apps.integrations.sync import sync_repository
    from apps.integrations.tests.fakes import FakeGitProvider

    provider = FakeGitProvider(
        commits=[make_remote_commit("s1", minutes_ago=5, files=[make_remote_file("src/alpha/a.py")])]
    )

    sync_repository(repository, provider=provider)

    module = Module.objects.get(project=repository.project, path_prefix="src/alpha")
    assert module.centrality_score == 1.0
