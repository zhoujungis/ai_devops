"""The correlation chain.

This is the product's core asset: the one implementation of

    Commit -> Module -> {TestCases, Bugs, Requirement, Releases}

Everything that reasons about a commit reads through here — the Code Impact Agent,
the risk engine and the dashboard — so they can never disagree about what a commit
affects. A second implementation would inevitably drift.

The joins are deterministic, not vector search. "Which tests must re-run" has to be
reproducible and auditable; similarity only ever *adds* candidates to this result.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.db.models import QuerySet

from apps.bugs.models import Bug, BugModuleLink, Severity
from apps.codebase.models import Commit, CommitModuleImpact, Module
from apps.core.models import LinkSource
from apps.releases.models import Release
from apps.requirements.models import ModuleRequirementLink, Requirement
from apps.testing.models import (
    TestCase,
    TestCaseCommitLink,
    TestCaseModuleLink,
    TestCaseRequirementLink,
    TestCaseStatus,
)

DEFAULT_REGRESSION_LIMIT = 20
DEFAULT_BUG_LIMIT = 20

#: How much to trust an edge, by how it was established.
SOURCE_WEIGHT: dict[str, float] = {
    LinkSource.MANUAL: 1.0,
    LinkSource.INFERRED: 0.75,
    LinkSource.AI: 0.5,
}

#: A regression score is a weighted composition, not a clamped sum. Summing and
#: clamping looks fine until the base already sits at 1.0 — then a second piece of
#: evidence moves nothing and the ranking silently ignores it. These weights add up
#: to 1.0, so more evidence always means a strictly higher score.
MODULE_WEIGHT = 0.60
VERIFIED_NEIGHBOUR_WEIGHT = 0.25
SAME_REQUIREMENT_WEIGHT = 0.15


@dataclass(frozen=True)
class ModuleImpact:
    module: Module
    weight: float
    churn_lines: int
    file_count: int
    is_test_change: bool


@dataclass(frozen=True)
class RegressionCandidate:
    test_case: TestCase
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class HistoricalBug:
    bug: Bug
    shared_modules: tuple[str, ...]
    shared_confidence: float


@dataclass(frozen=True)
class CommitExplanation:
    """Everything the chain can say about one commit, plus what it could not."""

    commit: Commit
    modules: tuple[ModuleImpact, ...]
    regression_candidates: tuple[RegressionCandidate, ...]
    historical_bugs: tuple[HistoricalBug, ...]
    requirement: Requirement | None
    requirement_source: str
    releases: tuple[Release, ...]
    data_gaps: tuple[str, ...]


class CorrelationService:
    """Reads the chain. Every method is a pure query — nothing here writes."""

    def __init__(
        self,
        *,
        regression_limit: int = DEFAULT_REGRESSION_LIMIT,
        bug_limit: int = DEFAULT_BUG_LIMIT,
    ) -> None:
        self._regression_limit = regression_limit
        self._bug_limit = bug_limit

    # ------------------------------------------------------------------
    def explain_commit(self, commit: Commit) -> CommitExplanation:
        raw_impacts = list(
            CommitModuleImpact.objects.filter(commit=commit)
            .select_related("module")
            .order_by("-weight")
        )
        module_ids: list[Any] = [impact.module_id for impact in raw_impacts]
        impacts = tuple(
            ModuleImpact(
                module=impact.module,
                weight=impact.weight,
                churn_lines=impact.churn_lines,
                file_count=impact.file_count,
                is_test_change=impact.is_test_change,
            )
            for impact in raw_impacts
        )

        requirement, requirement_source = self._resolve_requirement(commit, module_ids)
        candidates = self._regression_candidates(
            module_ids, requirement_id=requirement.pk if requirement else None
        )
        bugs = self._historical_bugs(module_ids, exclude_commit=commit)
        releases = tuple(self._releases_for(commit))

        return CommitExplanation(
            commit=commit,
            modules=impacts,
            regression_candidates=candidates,
            historical_bugs=bugs,
            requirement=requirement,
            requirement_source=requirement_source,
            releases=releases,
            data_gaps=self._data_gaps(
                impacts=impacts,
                candidates=candidates,
                bugs=bugs,
                requirement=requirement,
                requirement_source=requirement_source,
                modules_from_any_commit=bool(module_ids),
            ),
        )

    def explain_sha(self, sha: str, *, project: Any) -> CommitExplanation | None:
        """Convenience for callers holding a sha rather than a commit row."""
        commit = (
            Commit.objects.filter(sha=sha, repository__project_id=project.pk)
            .select_related("repository", "repository__project", "requirement")
            .first()
        )
        if commit is None:
            return None
        return self.explain_commit(commit)

    # ------------------------------------------------------------------
    def _resolve_requirement(
        self, commit: Commit, module_ids: Sequence[Any]
    ) -> tuple[Requirement | None, str]:
        """The requirement this commit serves.

        The commit's own link is the fast path — it comes from a key a human typed
        into the message. Falling back to the module's requirements is weaker
        evidence, so the caller is told which path was taken.
        """
        if commit.requirement_id is not None:
            return commit.requirement, "commit"
        if not module_ids:
            return None, "none"

        link = (
            ModuleRequirementLink.objects.filter(module_id__in=module_ids)
            .select_related("requirement")
            .order_by("-confidence", "-created_at")
            .first()
        )
        if link is None:
            return None, "none"
        return link.requirement, "module"

    def _regression_candidates(
        self, module_ids: Sequence[Any], *, requirement_id: Any | None
    ) -> tuple[RegressionCandidate, ...]:
        if not module_ids:
            return ()

        scores: dict[Any, float] = {}
        reasons: dict[Any, list[str]] = defaultdict(list)
        test_cases: dict[Any, TestCase] = {}

        for module_link in (
            TestCaseModuleLink.objects.filter(module_id__in=module_ids)
            .select_related("test_case", "module")
            .exclude(test_case__status=TestCaseStatus.DEPRECATED)
        ):
            contribution = (
                MODULE_WEIGHT * SOURCE_WEIGHT.get(module_link.source, 0.5) * module_link.confidence
            )
            if contribution > scores.get(module_link.test_case_id, 0.0):
                scores[module_link.test_case_id] = contribution
                reasons[module_link.test_case_id] = [
                    f"covers module {module_link.module.path_prefix}"
                ]
            test_cases[module_link.test_case_id] = module_link.test_case

        # A test that verified a commit which touched the same modules.
        verified = (
            TestCaseCommitLink.objects.filter(commit__module_impacts__module_id__in=module_ids)
            .select_related("test_case")
            .exclude(test_case__status=TestCaseStatus.DEPRECATED)
            .distinct()
        )
        for commit_link in verified:
            if commit_link.test_case_id not in scores:
                continue  # Only strengthens candidates that already cover a module.
            scores[commit_link.test_case_id] += VERIFIED_NEIGHBOUR_WEIGHT
            reasons[commit_link.test_case_id].append("verified a commit in the same modules")

        if requirement_id is not None:
            linked_ids = TestCaseRequirementLink.objects.filter(
                requirement_id=requirement_id
            ).values_list("test_case_id", flat=True)
            for test_case_id in linked_ids:
                if test_case_id in scores:
                    scores[test_case_id] += SAME_REQUIREMENT_WEIGHT
                    reasons[test_case_id].append("also covers the same requirement")

        ranked = sorted(
            (test_case_id for test_case_id in scores if test_case_id in test_cases),
            key=lambda test_case_id: (-scores[test_case_id], str(test_case_id)),
        )[: self._regression_limit]

        return tuple(
            RegressionCandidate(
                test_case=test_cases[test_case_id],
                score=round(scores[test_case_id], 4),
                reasons=tuple(reasons[test_case_id]),
            )
            for test_case_id in ranked
        )

    def _historical_bugs(
        self, module_ids: Sequence[Any], *, exclude_commit: Commit
    ) -> tuple[HistoricalBug, ...]:
        if not module_ids:
            return ()

        shared: dict[Any, list[tuple[str, float]]] = defaultdict(list)
        bugs: dict[Any, Bug] = {}

        for module_link in (
            BugModuleLink.objects.filter(module_id__in=module_ids)
            .select_related("bug", "module")
            .exclude(bug__status__in=["closed", "resolved"])
        ):
            shared[module_link.bug_id].append(
                (module_link.module.path_prefix, module_link.confidence)
            )
            bugs[module_link.bug_id] = module_link.bug

        # A bug already tied to this very commit is not a *historical* one.
        already_linked = set(exclude_commit.bug_links.values_list("bug_id", flat=True))

        ranked = sorted(
            (bug_id for bug_id in shared if bug_id not in already_linked),
            key=lambda bug_id: (
                Severity(bugs[bug_id].severity).rank,
                -max(confidence for _, confidence in shared[bug_id]),
            ),
        )[: self._bug_limit]

        return tuple(
            HistoricalBug(
                bug=bugs[bug_id],
                shared_modules=tuple(path for path, _ in shared[bug_id]),
                shared_confidence=round(max(confidence for _, confidence in shared[bug_id]), 4),
            )
            for bug_id in ranked
        )

    def _releases_for(self, commit: Commit) -> QuerySet[Release]:
        return (
            Release.objects.filter(commit_links__commit=commit)
            .select_related("project")
            .order_by("-created_at")
        )

    @staticmethod
    def _data_gaps(
        *,
        impacts: tuple[ModuleImpact, ...],
        candidates: tuple[RegressionCandidate, ...],
        bugs: tuple[HistoricalBug, ...],
        requirement: Requirement | None,
        requirement_source: str,
        modules_from_any_commit: bool,
    ) -> tuple[str, ...]:
        """State plainly what the chain could not resolve.

        A caller — human or agent — must be able to tell "nothing to report" from
        "we have no data", so silence is never mistaken for a clean bill of health.
        """
        gaps: list[str] = []
        if not modules_from_any_commit:
            gaps.append(
                "No modules resolved for this commit; impact analysis has nothing to "
                "reason about. Check that the repository is synced and that the paths "
                "match a module pattern."
            )
        if not candidates:
            gaps.append("No test cases are linked to the touched modules.")
        if not bugs:
            gaps.append("No open bugs are linked to the touched modules.")
        if requirement is None:
            gaps.append(
                "No requirement resolved: the commit message carries no known "
                "requirement key, and no touched module declares one."
            )
        elif requirement_source == "module":
            gaps.append(
                f"Requirement {requirement.external_key} was inferred from a module "
                "association, not from the commit itself."
            )
        return tuple(gaps)
