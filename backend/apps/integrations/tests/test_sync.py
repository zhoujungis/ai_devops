"""Sync behaviour, including the resume invariant that makes backfill safe."""

from __future__ import annotations

from typing import Any, cast

import pytest

from apps.accounts.models import Project
from apps.accounts.tests.factories import OrganizationFactory, ProjectFactory
from apps.codebase.models import Branch, Commit, Module
from apps.integrations.git.base import GitProviderError, RemoteBranch, RemoteCommit
from apps.integrations.models import Repository, SyncStatus
from apps.integrations.sync import sync_repository
from apps.integrations.tests.factories import build_repository
from apps.integrations.tests.fakes import FakeGitProvider, failing_provider, make_remote_commit

pytestmark = pytest.mark.django_db


@pytest.fixture
def repository(db: Any) -> Repository:
    project = cast(Project, ProjectFactory(org=OrganizationFactory()))
    return build_repository(project)


def _commits(count: int = 5) -> list[RemoteCommit]:
    return [
        make_remote_commit(f"sha{index}", minutes_ago=10 * (index + 1), message=f"commit {index}")
        for index in range(count)
    ]


def test_initial_sync_ingests_history_and_derives_modules(repository: Repository) -> None:
    provider = FakeGitProvider(commits=_commits())

    result = sync_repository(repository, provider=provider)

    assert result.commits_created == 5
    assert result.truncated is False
    assert Commit.objects.filter(repository=repository).count() == 5
    assert Module.objects.filter(project=repository.project).count() == 1

    repository.refresh_from_db()
    assert repository.sync_status == SyncStatus.SUCCEEDED
    assert repository.last_synced_at is not None
    assert repository.sync_cursor["backfill_before"] is None


def test_a_second_sync_creates_nothing_new(repository: Repository) -> None:
    provider = FakeGitProvider(commits=_commits())
    sync_repository(repository, provider=provider)

    second = sync_repository(repository, provider=provider)

    assert second.commits_created == 0
    assert Commit.objects.filter(repository=repository).count() == 5


def test_a_new_commit_is_picked_up_incrementally(repository: Repository) -> None:
    provider = FakeGitProvider(commits=_commits())
    sync_repository(repository, provider=provider)

    provider.commits.insert(0, make_remote_commit("brand-new", minutes_ago=1))
    result = sync_repository(repository, provider=provider)

    assert result.commits_created == 1
    assert Commit.objects.filter(repository=repository, sha="brand-new").exists()


def test_a_truncated_backfill_resumes_without_skipping_history(repository: Repository) -> None:
    """The regression this design exists for.

    A budget-limited backfill walks downhill. If it resumed with a "newer than"
    boundary it could never reach the history it skipped, and those commits would
    be lost silently — so the cursor has to keep the downward end separately.
    """
    provider = FakeGitProvider(commits=_commits())

    first = sync_repository(repository, provider=provider, limit=2)

    assert first.truncated is True
    assert first.commits_created == 2
    assert Commit.objects.filter(repository=repository).count() == 2

    repository.refresh_from_db()
    assert repository.sync_cursor["backfill_before"] is not None

    second = sync_repository(repository, provider=provider, limit=100)

    assert second.commits_created == 3
    assert Commit.objects.filter(repository=repository).count() == 5
    for index in range(5):
        assert Commit.objects.filter(repository=repository, sha=f"sha{index}").exists()

    repository.refresh_from_db()
    assert repository.sync_cursor["backfill_before"] is None


def test_paging_follows_multiple_pages(repository: Repository) -> None:
    provider = FakeGitProvider(
        commits=[make_remote_commit(f"c{index}", minutes_ago=index + 1) for index in range(250)]
    )

    result = sync_repository(repository, provider=provider, limit=500)

    assert result.commits_created == 250
    assert result.pages_fetched >= 3


def test_the_sync_window_bounds_how_far_back_it_walks(repository: Repository) -> None:
    repository.sync_window_days = 1
    repository.save(update_fields=["sync_window_days"])
    provider = FakeGitProvider(
        commits=[
            make_remote_commit("recent", minutes_ago=60),
            make_remote_commit("ancient", minutes_ago=60 * 24 * 10),
        ]
    )

    result = sync_repository(repository, provider=provider)

    assert result.commits_created == 1
    assert Commit.objects.filter(repository=repository, sha="recent").exists()
    assert not Commit.objects.filter(repository=repository, sha="ancient").exists()


def test_branches_are_refreshed(repository: Repository) -> None:
    provider = FakeGitProvider(
        commits=[],
        branches=[RemoteBranch(name="main", sha="head-sha", is_default=True, protected=True)],
    )

    result = sync_repository(repository, provider=provider)

    assert result.branches_updated == 1
    branch = Branch.objects.get(repository=repository)
    assert branch.name == "main"
    assert branch.head_sha == "head-sha"
    assert branch.is_default is True
    assert branch.is_protected is True


def test_failure_marks_the_repository_and_propagates(repository: Repository) -> None:
    provider = failing_provider(GitProviderError("upstream exploded"))

    with pytest.raises(GitProviderError):
        sync_repository(repository, provider=provider)

    repository.refresh_from_db()
    assert repository.sync_status == SyncStatus.FAILED
    assert "upstream exploded" in repository.sync_error


def test_the_rate_limit_is_recorded(repository: Repository) -> None:
    provider = FakeGitProvider(commits=_commits())
    provider.rate_limit_remaining = 12

    result = sync_repository(repository, provider=provider)

    assert result.rate_limit_remaining == 12
