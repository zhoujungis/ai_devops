"""End-to-end sync against a real public repository.

This is the only test that proves the whole path works on real git data rather
than on fixtures: real pagination, real commit JSON, real patches, real module
paths. It is marked ``network`` and excluded from the default run, because it
depends on an external service and on the anonymous API rate limit.

    pytest -m network

Set ``GITHUB_TOKEN`` to lift the 60-requests-per-hour anonymous limit.
"""

from __future__ import annotations

import os
from typing import Any, cast

import pytest
from django.utils import timezone

from apps.accounts.models import Project
from apps.accounts.tests.factories import OrganizationFactory, ProjectFactory
from apps.codebase.models import Commit, CommitModuleImpact, Module
from apps.integrations.git.github import GitHubProvider
from apps.integrations.models import Repository, SyncStatus
from apps.integrations.sync import sync_repository
from apps.integrations.tests.factories import build_repository

pytestmark = [pytest.mark.network, pytest.mark.django_db]

PUBLIC_REPO = "octocat/Hello-World"


@pytest.fixture
def provider() -> Any:
    instance = GitHubProvider(token=os.environ.get("GITHUB_TOKEN") or None)
    yield instance
    instance.close()


@pytest.fixture(scope="module")
def default_branch() -> str:
    """Resolved once per run.

    The anonymous API budget is 60 requests per hour, and this suite spends most
    of its calls fetching commit details, so metadata lookups are cached.
    """
    instance = GitHubProvider(token=os.environ.get("GITHUB_TOKEN") or None)
    try:
        return instance.get_repository(PUBLIC_REPO).default_branch
    finally:
        instance.close()


@pytest.fixture
def repository(db: Any, default_branch: str) -> Repository:
    """A tracked repository pointed at the real repo, with its real branch name.

    Reading the default branch from the API rather than assuming ``main`` is not
    just politeness: a wrong branch name makes every listing a 404.
    """
    project = cast(Project, ProjectFactory(org=OrganizationFactory()))
    return build_repository(
        project,
        full_name=PUBLIC_REPO,
        default_branch=default_branch,
        # The repository is over a decade old, so the window has to reach back.
        sync_window_days=7300,
    )


def test_a_real_repository_syncs_and_produces_module_impacts(
    repository: Repository, provider: GitHubProvider
) -> None:
    result = sync_repository(repository, provider=provider, limit=5)

    repository.refresh_from_db()
    assert repository.sync_status == SyncStatus.SUCCEEDED
    assert repository.last_synced_at is not None
    assert result.commits_created > 0, "a real repository must have commits in a 20-year window"

    commits = Commit.objects.filter(repository=repository)
    assert commits.count() == result.commits_created

    commit = commits.first()
    assert commit is not None
    assert commit.sha
    assert commit.message
    assert commit.committed_at <= timezone.now()

    # Real diff data, not a fixture.
    assert commit.files.count() > 0, "a real commit must have changed files"
    for changed in commit.files.all():
        assert changed.path

    # Every ingested commit must map onto at least one module, and the impact
    # weights are a share of the commit so they sum to one.
    impacts = CommitModuleImpact.objects.filter(commit=commit)
    assert impacts.exists()
    assert sum(impact.weight for impact in impacts) == pytest.approx(1.0, abs=0.01)
    for impact in impacts:
        assert impact.module.path_prefix
        assert impact.churn_lines >= 0
        assert impact.file_count > 0

    assert Module.objects.filter(project=repository.project).exists()


def test_a_second_live_sync_does_not_duplicate_history(
    repository: Repository, provider: GitHubProvider
) -> None:
    first = sync_repository(repository, provider=provider, limit=3)
    second = sync_repository(repository, provider=provider, limit=3)

    assert first.commits_created > 0
    assert second.commits_created == 0
    assert Commit.objects.filter(repository=repository).count() == first.commits_created


def test_branches_come_back_from_a_real_repository(
    repository: Repository, provider: GitHubProvider
) -> None:
    sync_repository(repository, provider=provider, limit=1)

    branches = repository.branches.all()
    assert branches.exists()
    assert branches.filter(is_default=True).exists()


def test_credentials_are_checked_against_the_real_host(provider: GitHubProvider) -> None:
    # No token configured is still "reachable and usable" for a public repository.
    assert provider.verify() is True
