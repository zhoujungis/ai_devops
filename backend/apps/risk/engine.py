"""The risk engine: measurable signals in, an explainable score out.

Design rule that shapes everything here: **the score is computed, never generated**.
Every input is a number read from the database, every signal has a documented
normalisation, and the weights are configuration. The AI layer explains this score;
it does not produce one. That is what makes "risk: high" defensible rather than a
language model's opinion.

Signals are normalised to 0..1 and weighted so the weights sum to 100, which means
the contributions sum *exactly* to the score. A dashboard can therefore show the
breakdown as an honest decomposition rather than an illustration of it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db.models import Avg, Count, Max, Min, Q
from django.utils import timezone

from apps.bugs.models import BugModuleLink, BugStatus, Severity
from apps.codebase.models import Commit, CommitModuleImpact, Module
from apps.risk.models import RiskLevel, RiskRule

#: Days of history that "recent" means for change frequency.
CHANGE_FREQUENCY_WINDOW_DAYS = 14
#: Days within which an upcoming release is considered imminent.
RELEASE_PROXIMITY_WINDOW_DAYS = 14
#: Hours after which synced data is considered stale.
FRESHNESS_WINDOW_HOURS = 24
#: Cases per module that count as "adequately covered".
CASES_PER_MODULE_TARGET = 3

DEFAULT_WEIGHTS: dict[str, float] = {
    "change_volume": 15.0,
    "historical_bug_density": 15.0,
    "test_failure_rate": 15.0,
    "module_centrality": 10.0,
    "change_frequency": 10.0,
    "coverage_gap": 10.0,
    "open_high_severity_bugs": 10.0,
    "test_coverage_gap": 5.0,
    "release_proximity": 5.0,
    "data_freshness_penalty": 5.0,
}

#: Human-readable meaning of each signal, surfaced in the breakdown so a reader
#: never has to guess what a number refers to.
SIGNAL_LABELS: dict[str, str] = {
    "change_volume": "Size of the change (lines and files touched)",
    "historical_bug_density": "Defects previously linked to the touched modules",
    "test_failure_rate": "Recent failure rate of tests covering these modules",
    "module_centrality": "How central these modules are to the codebase",
    "change_frequency": "How often these modules changed recently",
    "coverage_gap": "Share of these modules not covered by tests",
    "open_high_severity_bugs": "Unresolved S1/S2 defects in these modules",
    "test_coverage_gap": "Modules touched that have too few test cases",
    "release_proximity": "How soon a release is planned",
    "data_freshness_penalty": "How stale the synced data is",
}

#: Raw value at which a signal is considered saturated (normalised to 1.0).
SATURATION_POINTS: dict[str, float] = {
    "change_volume": 1000.0,
    "historical_bug_density": 5.0,
    "module_centrality": 1.0,
    "change_frequency": 30.0,
    "open_high_severity_bugs": 5.0,
}


class RiskLevels:
    """Thresholds, kept in one place so the UI and the API cannot disagree."""

    CRITICAL = 75.0
    HIGH = 50.0
    MEDIUM = 25.0

    @classmethod
    def of(cls, score: float) -> str:
        if score >= cls.CRITICAL:
            return RiskLevel.CRITICAL
        if score >= cls.HIGH:
            return RiskLevel.HIGH
        if score >= cls.MEDIUM:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW


@dataclass(frozen=True)
class Signal:
    """One measured input, with enough context to be argued with."""

    code: str
    raw: float
    normalized: float
    detail: str

    @property
    def label(self) -> str:
        return SIGNAL_LABELS.get(self.code, self.code)


@dataclass(frozen=True)
class Contribution:
    code: str
    label: str
    raw: float
    normalized: float
    weight: float
    contribution: float
    detail: str


@dataclass(frozen=True)
class RiskAssessment:
    score: float
    level: str
    contributions: tuple[Contribution, ...]

    def as_breakdown(self) -> list[dict[str, object]]:
        return [
            {
                "signal": item.code,
                "label": item.label,
                "raw": round(item.raw, 4),
                "normalized": round(item.normalized, 4),
                "weight": item.weight,
                "contribution": item.contribution,
                "detail": item.detail,
            }
            for item in self.contributions
        ]


def saturate(value: float, *, at: float) -> float:
    """Linear ramp from 0 to 1, clamped. ``at`` is where the signal maxes out."""
    if at <= 0:
        return 0.0
    return max(0.0, min(1.0, value / at))


class RiskEngine:
    """Computes risk for a commit or a module.

    Reads configuration from :class:`~apps.risk.models.RiskRule` so a team can
    retune weights without a deploy, and falls back to :data:`DEFAULT_WEIGHTS`.
    """

    def __init__(self, *, now: datetime | None = None) -> None:
        self._now = now or timezone.now()
        # One assessment reads the same release schedule and sync staleness three
        # times over (raw value, normalised value, and detail text). Memoising per
        # instance turns those into one query each; an instance is per-request, so
        # there is no staleness concern.
        self._release_cache: dict[Any, int | None] = {}
        self._staleness_cache: dict[Any, float] = {}

    # ------------------------------------------------------------------
    def assess_commit(self, commit: Commit) -> RiskAssessment:
        module_ids: list[Any] = list(
            CommitModuleImpact.objects.filter(commit=commit).values_list("module_id", flat=True)
        )
        signals = self._commit_signals(commit, module_ids)
        return self._score(commit.repository.project, signals)

    def assess_module(self, module: Module) -> RiskAssessment:
        signals = self._module_signals(module)
        return self._score(module.project, signals)

    # ------------------------------------------------------------------
    # signals
    # ------------------------------------------------------------------
    def _commit_signals(self, commit: Commit, module_ids: Sequence[Any]) -> list[Signal]:
        churn = commit.additions + commit.deletions
        recent_cutoff = self._now - timedelta(days=CHANGE_FREQUENCY_WINDOW_DAYS)

        recent_changes = CommitModuleImpact.objects.filter(
            module_id__in=module_ids, commit__committed_at__gte=recent_cutoff
        ).count()

        bug_count = BugModuleLink.objects.filter(module_id__in=module_ids).count()
        severity_count = (
            BugModuleLink.objects.filter(module_id__in=module_ids)
            .exclude(bug__status__in=[BugStatus.CLOSED, BugStatus.RESOLVED])
            .filter(bug__severity__in=[Severity.S1, Severity.S2])
            .values("bug_id")
            .distinct()
            .count()
        )

        failure_rate = self._test_failure_rate(module_ids)
        centrality = (
            Module.objects.filter(pk__in=module_ids).aggregate(value=Max("centrality_score"))[
                "value"
            ]
            or 0.0
        )
        coverage_gap = self._coverage_gap(module_ids, commit)
        case_gap = self._test_coverage_gap(module_ids)

        return [
            Signal(
                code="change_volume",
                raw=float(churn),
                normalized=saturate(churn, at=SATURATION_POINTS["change_volume"]),
                detail=f"{commit.files_changed} files, {churn} lines changed",
            ),
            Signal(
                code="historical_bug_density",
                raw=float(bug_count),
                normalized=(
                    saturate(
                        bug_count / max(len(module_ids), 1),
                        at=SATURATION_POINTS["historical_bug_density"],
                    )
                    if module_ids
                    else 0.0
                ),
                detail=f"{bug_count} defects ever linked to these modules",
            ),
            Signal(
                code="test_failure_rate",
                raw=failure_rate,
                normalized=failure_rate,
                detail=f"{failure_rate:.0%} of recent results for covering tests failed",
            ),
            Signal(
                code="module_centrality",
                raw=centrality,
                normalized=saturate(centrality, at=SATURATION_POINTS["module_centrality"]),
                detail=f"highest centrality among touched modules: {centrality:.2f}",
            ),
            Signal(
                code="change_frequency",
                raw=float(recent_changes),
                normalized=saturate(recent_changes, at=SATURATION_POINTS["change_frequency"]),
                detail=f"{recent_changes} changes in the last {CHANGE_FREQUENCY_WINDOW_DAYS} days",
            ),
            Signal(
                code="coverage_gap",
                raw=1.0 - coverage_gap["covered"],
                normalized=coverage_gap["gap"],
                detail=coverage_gap["detail"],
            ),
            Signal(
                code="open_high_severity_bugs",
                raw=float(severity_count),
                normalized=saturate(
                    severity_count, at=SATURATION_POINTS["open_high_severity_bugs"]
                ),
                detail=f"{severity_count} unresolved S1/S2 defects in these modules",
            ),
            Signal(
                code="test_coverage_gap",
                raw=case_gap["thin_modules"],
                normalized=case_gap["gap"],
                detail=case_gap["detail"],
            ),
            Signal(
                code="release_proximity",
                raw=self._release_raw(commit.repository.project),
                normalized=self._release_proximity(commit.repository.project),
                detail=self._release_detail(commit.repository.project),
            ),
            Signal(
                code="data_freshness_penalty",
                raw=self._staleness_hours(commit),
                normalized=self._freshness_penalty(commit),
                detail=self._freshness_detail(commit),
            ),
        ]

    def _module_signals(self, module: Module) -> list[Signal]:
        recent_cutoff = self._now - timedelta(days=CHANGE_FREQUENCY_WINDOW_DAYS)
        recent_changes = CommitModuleImpact.objects.filter(
            module=module, commit__committed_at__gte=recent_cutoff
        ).count()
        churn = (
            CommitModuleImpact.objects.filter(module=module).aggregate(value=Avg("churn_lines"))[
                "value"
            ]
            or 0.0
        )
        bug_count = BugModuleLink.objects.filter(module=module).count()
        severity_count = (
            BugModuleLink.objects.filter(module=module)
            .exclude(bug__status__in=[BugStatus.CLOSED, BugStatus.RESOLVED])
            .filter(bug__severity__in=[Severity.S1, Severity.S2])
            .values("bug_id")
            .distinct()
            .count()
        )
        coverage_gap = self._coverage_gap([module.pk], None)
        case_gap = self._test_coverage_gap([module.pk])
        failure_rate = self._test_failure_rate([module.pk])

        return [
            Signal(
                code="change_volume",
                raw=churn,
                normalized=saturate(churn, at=SATURATION_POINTS["change_volume"]),
                detail=f"average {churn:.0f} lines changed per commit",
            ),
            Signal(
                code="historical_bug_density",
                raw=float(bug_count),
                normalized=saturate(bug_count, at=SATURATION_POINTS["historical_bug_density"]),
                detail=f"{bug_count} defects ever linked to this module",
            ),
            Signal(
                code="test_failure_rate",
                raw=failure_rate,
                normalized=failure_rate,
                detail=f"{failure_rate:.0%} of recent results for covering tests failed",
            ),
            Signal(
                code="module_centrality",
                raw=module.centrality_score,
                normalized=saturate(
                    module.centrality_score, at=SATURATION_POINTS["module_centrality"]
                ),
                detail=f"centrality {module.centrality_score:.2f}",
            ),
            Signal(
                code="change_frequency",
                raw=float(recent_changes),
                normalized=saturate(recent_changes, at=SATURATION_POINTS["change_frequency"]),
                detail=f"{recent_changes} changes in the last {CHANGE_FREQUENCY_WINDOW_DAYS} days",
            ),
            Signal(
                code="coverage_gap",
                raw=1.0 - coverage_gap["covered"],
                normalized=coverage_gap["gap"],
                detail=coverage_gap["detail"],
            ),
            Signal(
                code="open_high_severity_bugs",
                raw=float(severity_count),
                normalized=saturate(
                    severity_count, at=SATURATION_POINTS["open_high_severity_bugs"]
                ),
                detail=f"{severity_count} unresolved S1/S2 defects",
            ),
            Signal(
                code="test_coverage_gap",
                raw=case_gap["thin_modules"],
                normalized=case_gap["gap"],
                detail=case_gap["detail"],
            ),
            Signal(
                code="release_proximity",
                raw=self._release_raw(module.project),
                normalized=self._release_proximity(module.project),
                detail=self._release_detail(module.project),
            ),
            Signal(
                code="data_freshness_penalty",
                raw=self._staleness_hours(None, module.project),
                normalized=self._freshness_penalty(None, module.project),
                detail=self._freshness_detail(None, module.project),
            ),
        ]

    # ------------------------------------------------------------------
    # individual measurements
    # ------------------------------------------------------------------
    def _test_failure_rate(self, module_ids: Sequence[Any]) -> float:
        """Failure rate of recent results for cases linked to these modules."""
        from apps.testing.models import TestResult

        totals = (
            TestResult.objects.filter(test_case__module_links__module_id__in=module_ids)
            .exclude(status="skipped")
            .aggregate(
                total=Count("id"),
                failed=Count("id", filter=Q(status__in=["failed", "error"])),
            )
        )
        total = totals["total"] or 0
        if total == 0:
            return 0.0
        return (totals["failed"] or 0) / total

    def _coverage_gap(self, module_ids: Sequence[Any], commit: Commit | None) -> dict[str, Any]:
        from apps.testing.models import CoverageSnapshot

        latest = (
            CoverageSnapshot.objects.filter(module_id__in=module_ids)
            .order_by("-captured_at")
            .values("module_id")
            .annotate(rate=Max("line_rate"))
        )
        rows = list(latest)
        if not rows:
            # No coverage data is not the same as perfect coverage, but it must not
            # silently inflate risk either: it is reported as a gap of unknown size.
            return {"covered": 0.0, "gap": 0.5, "detail": "no coverage data for these modules"}

        worst = min(float(row["rate"] or 0.0) for row in rows)
        return {
            "covered": worst,
            "gap": max(0.0, min(1.0, 1.0 - worst)),
            "detail": f"lowest line coverage among touched modules: {worst:.0%}",
        }

    def _test_coverage_gap(self, module_ids: Sequence[Any]) -> dict[str, Any]:
        from apps.testing.models import TestCaseModuleLink

        counts = dict(
            TestCaseModuleLink.objects.filter(module_id__in=module_ids)
            .values_list("module_id")
            .annotate(total=Count("id"))
        )
        thin = sum(
            1 for module_id in module_ids if counts.get(module_id, 0) < CASES_PER_MODULE_TARGET
        )
        if not module_ids:
            return {"thin_modules": 0, "gap": 0.0, "detail": "no modules resolved"}
        return {
            "thin_modules": float(thin),
            "gap": thin / len(module_ids),
            "detail": (
                f"{thin} of {len(module_ids)} touched modules have fewer than "
                f"{CASES_PER_MODULE_TARGET} test cases"
            ),
        }

    def _days_to_release(self, project: Any) -> int | None:
        from apps.releases.models import Release, ReleaseStatus

        if project is None:
            return None
        key = project.pk
        if key in self._release_cache:
            return self._release_cache[key]
        upcoming = Release.objects.filter(
            project=project, status=ReleaseStatus.PLANNED, planned_at__gte=self._now
        ).aggregate(soonest=Min("planned_at"))["soonest"]
        days = None if upcoming is None else max(0, (upcoming - self._now).days)
        self._release_cache[key] = days
        return days

    def _release_proximity(self, project: Any | None = None) -> float:
        days = self._days_to_release(project)
        if days is None:
            return 0.0
        return max(0.0, 1.0 - saturate(days, at=RELEASE_PROXIMITY_WINDOW_DAYS))

    def _release_raw(self, project: Any | None = None) -> float:
        """Days until the next release, or 0 when none is planned.

        Zero rather than a sentinel because "no release planned" is genuinely the
        least risky state for this signal, not an absence of data.
        """
        days = self._days_to_release(project)
        return float(days) if days is not None else 0.0

    def _release_detail(self, project: Any | None = None) -> str:
        days = self._days_to_release(project)
        if days is None:
            return "no planned release"
        return f"a release is planned in {days} days"

    def _staleness_hours(self, commit: Commit | None, project: Any | None = None) -> float:
        from apps.integrations.models import Repository

        target = project if project is not None else (commit.repository.project if commit else None)
        if target is None:
            return float(FRESHNESS_WINDOW_HOURS)
        key = target.pk
        if key in self._staleness_cache:
            return self._staleness_cache[key]
        newest = Repository.objects.filter(project=target).aggregate(latest=Max("last_synced_at"))[
            "latest"
        ]
        hours = (
            float(FRESHNESS_WINDOW_HOURS)
            if newest is None
            else max(0.0, (self._now - newest).total_seconds() / 3600.0)
        )
        self._staleness_cache[key] = hours
        return hours

    def _freshness_penalty(self, commit: Commit | None, project: Any | None = None) -> float:
        return saturate(self._staleness_hours(commit, project), at=float(FRESHNESS_WINDOW_HOURS))

    def _freshness_detail(self, commit: Commit | None, project: Any | None = None) -> str:
        hours = self._staleness_hours(commit, project)
        return f"repository last synced {hours:.1f} hours ago"

    # ------------------------------------------------------------------
    # scoring
    # ------------------------------------------------------------------
    def _weights(self, project: Any) -> dict[str, float]:
        weights = dict(DEFAULT_WEIGHTS)
        for rule in RiskRule.objects.for_project(project):
            if rule.code in weights:
                weights[rule.code] = rule.weight
        return weights

    def _score(self, project: Any, signals: list[Signal]) -> RiskAssessment:
        """Score the signals, with the reported weights always summing to 100.

        Weights are normalised here rather than trusted as configured. A team that
        sets weights summing to 500 still gets a 0-100 score, and — more importantly —
        the invariant that the contributions add up to the score survives *any*
        configuration. Clamping the total instead would quietly break that invariant
        the moment someone raised the weights.
        """
        configured = self._weights(project)
        requested = {signal.code: configured.get(signal.code, 0.0) for signal in signals}
        total = sum(requested.values())

        contributions = tuple(
            Contribution(
                code=signal.code,
                label=signal.label,
                raw=signal.raw,
                normalized=signal.normalized,
                weight=round(requested[signal.code] / total * 100, 6) if total > 0 else 0.0,
                contribution=(
                    round(signal.normalized * requested[signal.code] / total * 100, 6)
                    if total > 0
                    else 0.0
                ),
                detail=signal.detail,
            )
            for signal in signals
        )
        score = sum(item.contribution for item in contributions)
        return RiskAssessment(score=score, level=RiskLevels.of(score), contributions=contributions)
