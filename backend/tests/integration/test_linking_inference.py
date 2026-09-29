"""The inferred edges.

A commit message naming a defect is the same kind of human-supplied evidence a
requirement key is. Wiring that up is what gives the risk engine's bug-density and
open-severity signals — and the correlation engine's "this test verified a commit"
evidence — a writer other than the ORM.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from apps.accounts.models import Project
from apps.accounts.tests.factories import OrganizationFactory, ProjectFactory
from apps.bugs.models import Bug, BugCommitLink, BugModuleLink, BugRequirementLink, Severity
from apps.codebase.models import Commit, Module
from apps.core.models import LinkSource
from apps.integrations.models import Repository
from apps.integrations.sync import sync_repository
from apps.integrations.tests.factories import build_repository
from apps.integrations.tests.fakes import FakeGitProvider, make_remote_commit, make_remote_file
from apps.requirements.models import Requirement

pytestmark = pytest.mark.django_db


@pytest.fixture
def scene() -> tuple[Project, Repository, Bug]:
    project = cast(Project, ProjectFactory(org=OrganizationFactory()))
    repository = build_repository(project)
    Requirement.objects.create(project=project, external_key="PAY-18", title="Retry payments")
    bug = Bug.objects.create(project=project, key="QAC-201", title="502 on a cold upstream")
    return project, repository, bug


def _sync(repository: Repository, message: str) -> None:
    provider = FakeGitProvider(
        commits=[
            make_remote_commit(
                "c1", minutes_ago=5, message=message, files=[make_remote_file("src/payment/a.py")]
            )
        ]
    )
    sync_repository(repository, provider=provider)


def test_a_named_defect_is_linked_to_its_commit_module_and_requirement(
    scene: tuple[Project, Repository, Bug],
) -> None:
    project, repository, bug = scene

    _sync(repository, "PAY-18 fix QAC-201 retry storm")

    commit = Commit.objects.get(repository=repository, sha="c1")
    assert BugCommitLink.objects.filter(
        bug=bug, commit=commit, source=LinkSource.INFERRED
    ).exists()
    # "This defect lives in these modules" is what the risk engine reads.
    assert BugModuleLink.objects.filter(bug=bug, module__path_prefix="src/payment").exists()
    assert BugRequirementLink.objects.filter(
        bug=bug, requirement__external_key="PAY-18"
    ).exists()
    assert Module.objects.filter(project=project, path_prefix="src/payment").exists()


def test_a_message_naming_no_known_defect_links_nothing(
    scene: tuple[Project, Repository, Bug],
) -> None:
    _project, repository, _bug = scene

    _sync(repository, "fix QAC-999 something unrelated")

    assert BugCommitLink.objects.count() == 0


def test_resyncing_does_not_duplicate_the_inferred_edges(
    scene: tuple[Project, Repository, Bug],
) -> None:
    _project, repository, bug = scene

    _sync(repository, "QAC-201 again")
    _sync(repository, "QAC-201 again")

    assert BugCommitLink.objects.filter(bug=bug).count() == 1
    assert BugModuleLink.objects.filter(bug=bug).count() == 1


def test_the_defect_key_is_matched_case_insensitively(
    scene: tuple[Project, Repository, Bug],
) -> None:
    _project, repository, bug = scene

    _sync(repository, "qac-201 again")

    assert BugCommitLink.objects.filter(bug=bug).exists()


def test_severity_still_comes_from_the_defect_not_the_message(
    scene: tuple[Project, Repository, Bug],
) -> None:
    """The link is evidence about *where* the defect lives, not a re-grading of it."""
    _project, repository, bug = scene

    _sync(repository, "QAC-201 (s1!) pls fix")

    bug.refresh_from_db()
    assert bug.severity == Severity.S3


def test_an_empty_message_links_nothing(scene: tuple[Project, Repository, Any]) -> None:
    _project, repository, _bug = scene

    _sync(repository, "")

    assert BugCommitLink.objects.count() == 0
