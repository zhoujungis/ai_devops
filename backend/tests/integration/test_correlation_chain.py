"""The correlation chain, over a hand-built graph.

This is the acceptance test for S3. It builds

    commit -> module -> {test cases, bugs, requirement, release}

by hand and asserts the service walks the whole thing — which is the one thing the
rest of the product is built on.
"""

from __future__ import annotations

import itertools
from typing import Any, cast

import pytest
from django.utils import timezone

from apps.accounts.models import Project
from apps.accounts.tests.factories import OrganizationFactory, ProjectFactory
from apps.bugs.models import Bug, BugModuleLink, BugStatus, Severity
from apps.codebase.ingest import ingest_commit
from apps.codebase.models import Commit, Module
from apps.core.enums import Priority
from apps.core.models import LinkSource
from apps.integrations.tests.factories import build_repository
from apps.integrations.tests.fakes import make_remote_commit, make_remote_file
from apps.releases.models import Release, ReleaseCommitLink, ReleaseStatus
from apps.requirements.models import ModuleRequirementLink, Requirement, RequirementStatus
from apps.testing.models import TestCase, TestCaseCommitLink, TestCaseModuleLink, TestCaseStatus
from services.correlation import (
    MODULE_WEIGHT,
    SAME_REQUIREMENT_WEIGHT,
    VERIFIED_NEIGHBOUR_WEIGHT,
    CorrelationService,
)
from services.linking import find_requirement_key, link_commit_to_requirement

pytestmark = pytest.mark.django_db

PAYMENT_FILE = "src/payment/PaymentService.java"
ORDER_FILE = "src/order/OrderService.java"

#: Distinct commit ages, so ordering within a test is never ambiguous.
_AGES = itertools.count(start=1)


def _commit(project: Project, sha: str, *, files: list[str], message: str = "change") -> Commit:
    repository = project.repositories.first()
    assert repository is not None
    return ingest_commit(
        repository,
        make_remote_commit(
            sha,
            minutes_ago=next(_AGES) * 5,
            message=message,
            files=[make_remote_file(path) for path in files],
        ),
    )


@pytest.fixture
def project(db: Any) -> Project:
    project = cast(Project, ProjectFactory(org=OrganizationFactory()))
    build_repository(project)
    return project


@pytest.fixture
def graph(project: Project) -> dict[str, Any]:
    """A payment commit with a requirement, two test cases, a bug and a release."""
    commit = _commit(project, "abc123", files=[PAYMENT_FILE], message="PAY-18 retry payment")

    requirement = Requirement.objects.create(
        project=project,
        external_key="PAY-18",
        title="Retry failed payments",
        status=RequirementStatus.APPROVED,
        priority=Priority.P0,
    )
    requirement.items.create(seq=1, text="A failed capture is retried three times.")

    # The sync pipeline links commits to requirements right after ingestion; do the
    # same here so the fixture exercises the fast path rather than a shortcut.
    link_commit_to_requirement(commit)

    module = Module.objects.get(project=project, path_prefix="src/payment")
    ModuleRequirementLink.objects.create(
        requirement=requirement, module=module, source=LinkSource.MANUAL, confidence=1.0
    )

    covered = TestCase.objects.create(project=project, key="TC-001", title="Payment succeeds")
    verified = TestCase.objects.create(project=project, key="TC-002", title="Payment retried")
    for test_case in (covered, verified):
        TestCaseModuleLink.objects.create(
            test_case=test_case, module=module, source=LinkSource.MANUAL, confidence=1.0
        )

    # A previous commit in the same module that TC-002 already verified.
    earlier = _commit(project, "def456", files=[PAYMENT_FILE])
    TestCaseCommitLink.objects.create(
        test_case=verified, commit=earlier, source=LinkSource.MANUAL, confidence=1.0
    )

    bug = Bug.objects.create(
        project=project,
        key="BUG-1023",
        title="Duplicate charge on retry",
        severity=Severity.S1,
        status=BugStatus.OPEN,
    )
    BugModuleLink.objects.create(bug=bug, module=module, source=LinkSource.MANUAL, confidence=1.0)

    release = Release.objects.create(
        project=project, version="2026.09", status=ReleaseStatus.PLANNED
    )
    ReleaseCommitLink.objects.create(release=release, commit=commit)

    return {
        "commit": commit,
        "module": module,
        "requirement": requirement,
        "covered": covered,
        "verified": verified,
        "bug": bug,
        "release": release,
    }


# ---------------------------------------------------------------------------
def test_the_chain_resolves_end_to_end(graph: dict[str, Any]) -> None:
    explanation = CorrelationService().explain_commit(graph["commit"])

    assert [impact.module.path_prefix for impact in explanation.modules] == ["src/payment"]

    assert explanation.requirement is not None
    assert explanation.requirement.external_key == "PAY-18"
    assert explanation.requirement_source == "commit"

    keys = {candidate.test_case.key for candidate in explanation.regression_candidates}
    assert keys == {"TC-001", "TC-002"}

    assert [bug.bug.key for bug in explanation.historical_bugs] == ["BUG-1023"]
    assert explanation.historical_bugs[0].shared_modules == ("src/payment",)

    assert [release.version for release in explanation.releases] == ["2026.09"]


def test_every_claim_carries_a_reason(graph: dict[str, Any]) -> None:
    explanation = CorrelationService().explain_commit(graph["commit"])

    for candidate in explanation.regression_candidates:
        assert candidate.reasons, "a candidate with no stated reason is not explainable"
        assert 0 < candidate.score <= 1.0


def test_a_test_that_verified_the_same_area_ranks_higher(graph: dict[str, Any]) -> None:
    explanation = CorrelationService().explain_commit(graph["commit"])

    ranked = [candidate.test_case.key for candidate in explanation.regression_candidates]
    assert ranked[0] == "TC-002", "the test with prior verification evidence should lead"

    best, runner_up = explanation.regression_candidates[:2]
    assert any("verified a commit" in reason for reason in best.reasons)
    # The score is a composition of named signals, so extra evidence adds exactly
    # the weight it is worth rather than saturating at the first one.
    assert best.score == pytest.approx(MODULE_WEIGHT + VERIFIED_NEIGHBOUR_WEIGHT)
    assert runner_up.score == pytest.approx(MODULE_WEIGHT)
    assert best.score > runner_up.score


def test_the_score_weights_leave_no_headroom_above_one() -> None:
    total = MODULE_WEIGHT + VERIFIED_NEIGHBOUR_WEIGHT + SAME_REQUIREMENT_WEIGHT

    assert total == pytest.approx(1.0)


def test_the_requirement_falls_back_to_the_module(project: Project) -> None:
    """No key in the message, so the module association is the only evidence."""
    commit = _commit(project, "nokey", files=[PAYMENT_FILE], message="tidy up")
    requirement = Requirement.objects.create(
        project=project, external_key="PAY-99", title="Indirect"
    )
    module = Module.objects.get(project=project, path_prefix="src/payment")
    ModuleRequirementLink.objects.create(
        requirement=requirement, module=module, source=LinkSource.INFERRED, confidence=0.6
    )

    explanation = CorrelationService().explain_commit(commit)

    assert explanation.requirement is not None
    assert explanation.requirement.external_key == "PAY-99"
    assert explanation.requirement_source == "module"
    assert any("inferred from a module" in gap for gap in explanation.data_gaps)


def test_the_commit_link_wins_over_the_module_link(project: Project) -> None:
    direct = Requirement.objects.create(project=project, external_key="PAY-18", title="Direct")
    indirect = Requirement.objects.create(project=project, external_key="PAY-77", title="Indirect")
    commit = _commit(project, "direct", files=[PAYMENT_FILE], message="PAY-18 fix")
    commit.requirement = direct
    commit.save(update_fields=["requirement"])

    module = Module.objects.get(project=project, path_prefix="src/payment")
    ModuleRequirementLink.objects.create(requirement=indirect, module=module)

    explanation = CorrelationService().explain_commit(commit)

    assert explanation.requirement_source == "commit"
    assert explanation.requirement is not None
    assert explanation.requirement.external_key == "PAY-18"


def test_a_module_that_was_not_touched_yields_no_candidates(project: Project) -> None:
    commit = _commit(project, "payment-only", files=[PAYMENT_FILE])
    _commit(project, "order-only", files=[ORDER_FILE])
    order_module = Module.objects.get(project=project, path_prefix="src/order")

    irrelevant = TestCase.objects.create(project=project, key="TC-900", title="Order only")
    TestCaseModuleLink.objects.create(test_case=irrelevant, module=order_module)

    explanation = CorrelationService().explain_commit(commit)

    assert [impact.module.path_prefix for impact in explanation.modules] == ["src/payment"]
    assert explanation.regression_candidates == ()


def test_deprecated_test_cases_are_not_candidates(graph: dict[str, Any]) -> None:
    graph["covered"].status = TestCaseStatus.DEPRECATED
    graph["covered"].save(update_fields=["status"])

    explanation = CorrelationService().explain_commit(graph["commit"])

    assert [candidate.test_case.key for candidate in explanation.regression_candidates] == [
        "TC-002"
    ]


def test_closed_bugs_are_not_history(graph: dict[str, Any]) -> None:
    graph["bug"].status = BugStatus.CLOSED
    graph["bug"].save(update_fields=["status"])

    explanation = CorrelationService().explain_commit(graph["commit"])

    assert explanation.historical_bugs == ()


def test_severe_bugs_rank_above_minor_ones(graph: dict[str, Any]) -> None:
    minor = Bug.objects.create(
        project=graph["bug"].project,
        key="BUG-2000",
        title="Cosmetic",
        severity=Severity.S4,
        status=BugStatus.OPEN,
    )
    BugModuleLink.objects.create(bug=minor, module=graph["module"])

    explanation = CorrelationService().explain_commit(graph["commit"])

    assert [entry.bug.key for entry in explanation.historical_bugs] == ["BUG-1023", "BUG-2000"]


def test_data_gaps_are_stated_when_there_is_nothing_to_say(project: Project) -> None:
    """An empty chain must never read as a clean bill of health."""
    commit = _commit(project, "lonely", files=[PAYMENT_FILE])

    explanation = CorrelationService().explain_commit(commit)

    assert explanation.regression_candidates == ()
    assert explanation.historical_bugs == ()
    assert explanation.requirement is None
    joined = " ".join(explanation.data_gaps)
    assert "No test cases" in joined
    assert "No open bugs" in joined
    assert "No requirement resolved" in joined


def test_a_commit_with_no_modules_reports_the_gap(project: Project) -> None:
    repository = project.repositories.first()
    assert repository is not None
    commit = Commit.objects.create(
        repository=repository,
        sha="empty-diff",
        message="no files",
        committed_at=timezone.now(),
    )

    explanation = CorrelationService().explain_commit(commit)

    assert explanation.modules == ()
    assert any("No modules resolved" in gap for gap in explanation.data_gaps)


# ---------------------------------------------------------------------------
def test_the_requirement_linker_reads_the_commit_message(project: Project) -> None:
    requirement = Requirement.objects.create(project=project, external_key="PAY-18", title="Retry")
    commit = _commit(project, "msg", files=[PAYMENT_FILE], message="PAY-18: retry on timeout")

    linked = link_commit_to_requirement(commit)

    assert linked == requirement
    commit.refresh_from_db()
    assert commit.requirement_id == requirement.pk


def test_the_linker_ignores_unknown_keys(project: Project) -> None:
    commit = _commit(project, "unknown", files=[PAYMENT_FILE], message="FIX-1 and UTF-8 handling")

    assert link_commit_to_requirement(commit) is None
    commit.refresh_from_db()
    assert commit.requirement_id is None


def test_the_key_matcher_is_case_insensitive(project: Project) -> None:
    requirement = Requirement.objects.create(project=project, external_key="PAY-18", title="Retry")

    assert find_requirement_key("fix pay-18 now", {"PAY-18": requirement}) == requirement
