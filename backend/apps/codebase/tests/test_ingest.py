"""Commit ingestion: files, module resolution and the impact edges."""

from __future__ import annotations

from typing import Any, cast

import pytest

from apps.accounts.models import Project
from apps.accounts.tests.factories import OrganizationFactory, ProjectFactory
from apps.codebase.ingest import ingest_commit
from apps.codebase.models import Commit, CommitFile, CommitModuleImpact, Module
from apps.integrations.models import Repository
from apps.integrations.tests.factories import build_repository
from apps.integrations.tests.fakes import make_remote_commit, make_remote_file

pytestmark = pytest.mark.django_db


@pytest.fixture
def repository(db: Any) -> Repository:
    project = cast(Project, ProjectFactory(org=OrganizationFactory()))
    return build_repository(project)


def test_commit_files_and_modules_are_persisted(repository: Repository) -> None:
    remote = make_remote_commit(
        "abc123",
        minutes_ago=5,
        files=[
            make_remote_file("src/payment/PaymentService.java", additions=10, deletions=2),
            make_remote_file("src/payment/PaymentController.java", additions=4, deletions=0),
        ],
    )

    commit = ingest_commit(repository, remote)

    assert commit.sha == "abc123"
    assert commit.short_sha == "abc123"
    assert commit.files_changed == 2
    assert CommitFile.objects.filter(commit=commit).count() == 2

    module = Module.objects.get(project=repository.project)
    assert module.path_prefix == "src/payment"
    assert module.language == "java"

    impact = CommitModuleImpact.objects.get(commit=commit, module=module)
    assert impact.file_count == 2
    assert impact.churn_lines == 16
    assert impact.weight == pytest.approx(1.0)
    assert impact.is_test_change is False


def test_impact_weights_split_the_commit_across_modules(repository: Repository) -> None:
    remote = make_remote_commit(
        "def456",
        minutes_ago=5,
        files=[
            make_remote_file("src/payment/PaymentService.java", additions=9, deletions=1),
            make_remote_file("src/order/OrderService.java", additions=10, deletions=0),
        ],
    )

    commit = ingest_commit(repository, remote)

    impacts = {
        impact.module.path_prefix: impact
        for impact in CommitModuleImpact.objects.filter(commit=commit)
    }
    assert set(impacts) == {"src/payment", "src/order"}
    assert impacts["src/payment"].weight == pytest.approx(0.5)
    assert sum(impact.weight for impact in impacts.values()) == pytest.approx(1.0)


def test_a_commit_touching_only_tests_is_flagged(repository: Repository) -> None:
    remote = make_remote_commit(
        "tests-only",
        minutes_ago=5,
        files=[make_remote_file("src/payment/tests/PaymentServiceTest.java")],
    )

    commit = ingest_commit(repository, remote)

    assert CommitModuleImpact.objects.get(commit=commit).is_test_change is True


def test_a_mixed_commit_separates_test_modules_from_production(
    repository: Repository,
) -> None:
    """Package-rooted languages keep the full package path, so tests land in their
    own module rather than diluting the production module's test signal."""
    remote = make_remote_commit(
        "mixed",
        minutes_ago=5,
        files=[
            make_remote_file("src/payment/PaymentService.java"),
            make_remote_file("src/payment/tests/PaymentServiceTest.java"),
        ],
    )

    commit = ingest_commit(repository, remote)

    impacts = {
        impact.module.path_prefix: impact
        for impact in CommitModuleImpact.objects.filter(commit=commit)
    }
    assert set(impacts) == {"src/payment", "src/payment/tests"}
    assert impacts["src/payment/tests"].is_test_change is True
    assert impacts["src/payment"].is_test_change is False


def test_re_ingesting_the_same_commit_is_a_no_op(repository: Repository) -> None:
    remote = make_remote_commit("duplicate", minutes_ago=5)

    first = ingest_commit(repository, remote)
    second = ingest_commit(repository, remote)

    assert first.pk == second.pk
    assert Commit.objects.filter(repository=repository, sha="duplicate").count() == 1
    assert CommitFile.objects.filter(commit=first).count() == 1
    assert CommitModuleImpact.objects.filter(commit=first).count() == 1


def test_oversized_diffs_are_stored_truncated(repository: Repository, settings: Any) -> None:
    settings.GIT_PATCH_MAX_BYTES = 10
    remote = make_remote_commit(
        "huge", minutes_ago=5, files=[make_remote_file("src/a.py", patch="x" * 100)]
    )

    commit = ingest_commit(repository, remote)

    changed = CommitFile.objects.get(commit=commit)
    assert changed.truncated is True
    assert changed.patch == "x" * 10
    assert changed.has_patch is True


def test_a_file_without_a_diff_records_no_patch(repository: Repository) -> None:
    remote = make_remote_commit(
        "binary", minutes_ago=5, files=[make_remote_file("assets/logo.png", patch=None)]
    )

    commit = ingest_commit(repository, remote)

    changed = CommitFile.objects.get(commit=commit)
    assert changed.has_patch is False
    assert changed.patch == ""
    assert changed.truncated is False


def test_renames_with_no_churn_still_produce_weights(repository: Repository) -> None:
    remote = make_remote_commit(
        "rename",
        minutes_ago=5,
        files=[
            make_remote_file("src/alpha/moved.py", additions=0, deletions=0, change_type="rename"),
            make_remote_file("src/beta/moved.py", additions=0, deletions=0, change_type="rename"),
        ],
    )

    commit = ingest_commit(repository, remote)

    impacts = list(CommitModuleImpact.objects.filter(commit=commit))
    assert len(impacts) == 2
    assert sum(impact.weight for impact in impacts) == pytest.approx(1.0)
