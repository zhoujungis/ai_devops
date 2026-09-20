"""The risk engine.

Two properties are load-bearing and asserted directly: the contributions add up to
the score exactly, and every signal is explainable from data in the database. A
score that does not decompose is a score nobody can argue with.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

import pytest
from django.utils import timezone

from apps.accounts.models import Organization, Project
from apps.accounts.tests.factories import (
    MembershipFactory,
    OrganizationFactory,
    ProjectFactory,
    UserFactory,
)
from apps.bugs.models import Bug, BugModuleLink, BugStatus, Severity
from apps.codebase.ingest import ingest_commit
from apps.codebase.models import Commit, Module
from apps.core.models import LinkSource
from apps.integrations.tests.factories import build_repository
from apps.integrations.tests.fakes import make_remote_commit, make_remote_file
from apps.releases.models import Release, ReleaseStatus
from apps.risk.engine import DEFAULT_WEIGHTS, RiskEngine, RiskLevels, saturate
from apps.risk.models import RiskLevel, RiskRule
from apps.testing.models import TestCase, TestCaseModuleLink

pytestmark = pytest.mark.django_db

PAYMENT_FILE = "src/payment/PaymentService.java"


def _scene(*, modules: int = 1) -> tuple[Project, Commit]:
    org = cast(Organization, OrganizationFactory())
    MembershipFactory(org=org, user=UserFactory(), role="admin")
    project = cast(Project, ProjectFactory(org=org))
    repository = build_repository(project)
    files = [PAYMENT_FILE] if modules == 1 else [PAYMENT_FILE, "src/order/OrderService.java"]
    commit = ingest_commit(
        repository,
        make_remote_commit("abc123", minutes_ago=5, files=[make_remote_file(f) for f in files]),
    )
    return project, commit


def _weight_of(assessment: Any, code: str) -> float:
    """Effective (normalised) weight of one signal."""
    return next(item.weight for item in assessment.contributions if item.code == code)


# ---------------------------------------------------------------------------
# normalisation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "at", "expected"),
    [(0, 100, 0.0), (50, 100, 0.5), (100, 100, 1.0), (500, 100, 1.0), (-5, 100, 0.0)],
)
def test_saturation_ramps_and_clamps(value: float, at: float, expected: float) -> None:
    assert saturate(value, at=at) == expected


def test_a_zero_saturation_point_does_not_divide_by_zero() -> None:
    assert saturate(5, at=0) == 0.0


# ---------------------------------------------------------------------------
# the score decomposes exactly
# ---------------------------------------------------------------------------
def test_contributions_sum_exactly_to_the_score() -> None:
    _project, commit = _scene()

    assessment = RiskEngine().assess_commit(commit)

    assert sum(item.contribution for item in assessment.contributions) == pytest.approx(
        assessment.score, abs=1e-6
    )


def test_the_default_weights_sum_to_one_hundred() -> None:
    assert sum(DEFAULT_WEIGHTS.values()) == pytest.approx(100.0)


def test_every_signal_is_named_and_explained() -> None:
    _project, commit = _scene()

    assessment = RiskEngine().assess_commit(commit)

    assert {item.code for item in assessment.contributions} == set(DEFAULT_WEIGHTS)
    for item in assessment.contributions:
        assert item.label and item.label != item.code, f"{item.code} has no human label"
        assert item.detail, f"{item.code} has no stated detail"
        assert 0.0 <= item.normalized <= 1.0


def test_the_breakdown_is_serialisable_and_complete() -> None:
    _project, commit = _scene()

    breakdown = RiskEngine().assess_commit(commit).as_breakdown()

    assert len(breakdown) == len(DEFAULT_WEIGHTS)
    assert all(
        {"signal", "label", "raw", "normalized", "weight", "contribution", "detail"} <= set(row)
        for row in breakdown
    )


# ---------------------------------------------------------------------------
# signals actually move
# ---------------------------------------------------------------------------
def test_bugs_in_the_module_raise_the_score() -> None:
    project, commit = _scene()
    quiet = RiskEngine().assess_commit(commit).score

    module = Module.objects.get(project=project, path_prefix="src/payment")
    bug = Bug.objects.create(
        project=project, key="BUG-1", title="Charge twice", severity=Severity.S1
    )
    BugModuleLink.objects.create(bug=bug, module=module, source=LinkSource.MANUAL)

    assert RiskEngine().assess_commit(commit).score > quiet


def test_a_closed_bug_does_not_count_as_open_severity_risk() -> None:
    project, commit = _scene()
    module = Module.objects.get(project=project, path_prefix="src/payment")
    bug = Bug.objects.create(
        project=project,
        key="BUG-2",
        title="Fixed",
        severity=Severity.S1,
        status=BugStatus.CLOSED,
    )
    BugModuleLink.objects.create(bug=bug, module=module, source=LinkSource.MANUAL)

    signals = {item.code: item for item in RiskEngine().assess_commit(commit).contributions}

    assert signals["open_high_severity_bugs"].raw == 0
    # Historical density still counts it: it happened, whatever its current state.
    assert signals["historical_bug_density"].raw == 1


def test_an_upcoming_release_raises_the_score() -> None:
    project, commit = _scene()
    quiet = RiskEngine().assess_commit(commit).score

    Release.objects.create(
        project=project,
        version="2026.10",
        status=ReleaseStatus.PLANNED,
        planned_at=timezone.now() + timedelta(days=1),
    )

    assert RiskEngine().assess_commit(commit).score > quiet


def test_tests_covering_a_module_lower_the_test_coverage_gap() -> None:
    project, commit = _scene()
    module = Module.objects.get(project=project, path_prefix="src/payment")

    before = {item.code: item for item in RiskEngine().assess_commit(commit).contributions}[
        "test_coverage_gap"
    ]

    for index in range(3):
        case = TestCase.objects.create(project=project, key=f"TC-{index}", title=f"Case {index}")
        TestCaseModuleLink.objects.create(test_case=case, module=module)

    after = {item.code: item for item in RiskEngine().assess_commit(commit).contributions}[
        "test_coverage_gap"
    ]

    assert before.normalized == 1.0
    assert after.normalized == 0.0


def test_missing_coverage_data_is_a_mid_gap_not_perfect_coverage() -> None:
    """No data must not read as "fully covered" — silence is not evidence."""
    _project, commit = _scene()

    signal = {item.code: item for item in RiskEngine().assess_commit(commit).contributions}[
        "coverage_gap"
    ]

    assert signal.normalized == 0.5
    assert "no coverage data" in signal.detail


# ---------------------------------------------------------------------------
# levels
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.0, RiskLevel.LOW),
        (24.9, RiskLevel.LOW),
        (25.0, RiskLevel.MEDIUM),
        (49.9, RiskLevel.MEDIUM),
        (50.0, RiskLevel.HIGH),
        (74.9, RiskLevel.HIGH),
        (75.0, RiskLevel.CRITICAL),
        (100.0, RiskLevel.CRITICAL),
    ],
)
def test_level_thresholds(score: float, expected: str) -> None:
    assert RiskLevels.of(score) == expected


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
def test_a_project_rule_overrides_the_default_weight() -> None:
    project, commit = _scene()

    RiskRule.objects.create(project=project, code="change_volume", weight=0.0)
    baseline = RiskEngine().assess_commit(commit)
    volume = next(item for item in baseline.contributions if item.code == "change_volume")
    assert volume.weight == 0.0
    assert volume.contribution == 0.0


def test_weights_are_reported_normalised_to_one_hundred() -> None:
    """Whatever the configured weights, the reported ones are a 0-100 share."""
    _project, commit = _scene()

    assessment = RiskEngine().assess_commit(commit)

    assert sum(item.weight for item in assessment.contributions) == pytest.approx(100.0, abs=1e-6)


def test_an_organisation_rule_is_inherited_by_its_projects() -> None:
    project, commit = _scene()

    baseline = _weight_of(RiskEngine().assess_commit(commit), "change_volume")
    RiskRule.objects.create(org=project.org, code="change_volume", weight=1.0)
    after = _weight_of(RiskEngine().assess_commit(commit), "change_volume")

    assert after < baseline, "lowering the weight must lower its share"


def test_a_project_rule_wins_over_the_organisation_rule() -> None:
    project, commit = _scene()
    RiskRule.objects.create(org=project.org, code="change_volume", weight=1.0)
    from_org = _weight_of(RiskEngine().assess_commit(commit), "change_volume")

    RiskRule.objects.create(project=project, code="change_volume", weight=50.0)
    from_project = _weight_of(RiskEngine().assess_commit(commit), "change_volume")

    assert from_project > from_org


def test_a_disabled_rule_is_ignored() -> None:
    project, commit = _scene()
    RiskRule.objects.create(project=project, code="change_volume", weight=0.0, enabled=False)

    volume = next(
        item
        for item in RiskEngine().assess_commit(commit).contributions
        if item.code == "change_volume"
    )

    assert volume.weight == DEFAULT_WEIGHTS["change_volume"]


# ---------------------------------------------------------------------------
# modules
# ---------------------------------------------------------------------------
def test_a_module_can_be_assessed_on_its_own() -> None:
    project, _commit = _scene()
    module = Module.objects.get(project=project, path_prefix="src/payment")

    assessment = RiskEngine().assess_module(module)

    assert {item.code for item in assessment.contributions} == set(DEFAULT_WEIGHTS)
    assert sum(item.contribution for item in assessment.contributions) == pytest.approx(
        assessment.score, abs=1e-6
    )


def test_the_score_never_exceeds_one_hundred() -> None:
    project, commit = _scene()
    # Push every weight to the maximum and make the subject as bad as possible.
    for code in DEFAULT_WEIGHTS:
        RiskRule.objects.create(project=project, code=code, weight=100.0)

    assessment = RiskEngine().assess_commit(commit)

    assert assessment.score <= 100.0
