"""The write paths the trackers were missing.

Before these existed, ``TestCaseStep``, ``TestResult``, ``CoverageSnapshot``,
``BugOccurrence`` and ``ReleaseCommitLink`` had readers but no way in through the API —
so the risk engine's coverage and test-failure signals, and a release's shipped
commits, could only ever be fed by the ORM.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.bugs.models import Bug
from apps.codebase.models import Module
from apps.core.models import LinkSource
from apps.testing.models import TestCaseCommitLink
from tests.integration.test_s3_api import Scene, build_scene

pytestmark = pytest.mark.django_db


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _coverage_url(scene: Scene) -> str:
    base = f"/api/v1/orgs/{scene.org.pk}/projects/{scene.project.pk}"
    return f"{base}/coverage-snapshots"


# ---------------------------------------------------------------------------
# test cases and their steps
# ---------------------------------------------------------------------------
def test_test_case_steps_are_written_with_the_case() -> None:
    scene = build_scene(Role.DEVELOPER)
    client = _client(scene.actor)

    created = client.post(
        scene.urls()["test_case_list"],
        {
            "key": "TC-900",
            "title": "Retry the capture",
            "steps": [
                {"action": "force a timeout"},
                {"action": "assert the retry", "test_data": {"attempt": 2}},
            ],
        },
        format="json",
    )

    assert created.status_code == 201, created.content
    assert [step["action"] for step in created.json()["steps"]] == [
        "force a timeout",
        "assert the retry",
    ]
    assert [step["seq"] for step in created.json()["steps"]] == [1, 2]

    patched = client.patch(
        f"{scene.urls()['test_case_list']}/{created.json()['id']}",
        {"steps": [{"action": "only one step now"}]},
        format="json",
    )

    assert patched.status_code == 200
    assert [step["action"] for step in patched.json()["steps"]] == ["only one step now"]


# ---------------------------------------------------------------------------
# test runs and their results
# ---------------------------------------------------------------------------
def test_test_run_results_are_written_and_the_header_is_recounted() -> None:
    scene = build_scene(Role.QA)

    response = _client(scene.actor).post(
        scene.urls()["test_run_list"],
        {
            "environment": "ci",
            # Deliberately wrong: the header must be derived from the results.
            "total": 999,
            "passed": 999,
            "results": [
                {"status": "passed", "duration_ms": 5},
                {"status": "failed", "error_message": "boom"},
                {"status": "error"},
                {"status": "skipped"},
            ],
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["total"] == 4
    assert body["passed"] == 1
    assert body["failed"] == 2, "an errored result is a failure, not a pass"
    assert body["skipped"] == 1
    assert len(body["results"]) == 4


def test_a_result_cannot_reference_another_projects_test_case() -> None:
    scene = build_scene(Role.QA)
    other = build_scene(Role.QA)

    response = _client(scene.actor).post(
        scene.urls()["test_run_list"],
        {"results": [{"status": "passed", "test_case": str(other.test_case.pk)}]},
        format="json",
    )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# coverage snapshots
# ---------------------------------------------------------------------------
def test_coverage_snapshots_are_appended_and_never_edited() -> None:
    scene = build_scene(Role.QA)
    client = _client(scene.actor)
    module = Module.objects.get(project=scene.project, path_prefix="src/payment")
    url = _coverage_url(scene)

    created = client.post(
        url,
        {
            "module": str(module.pk),
            "line_rate": 0.71,
            "captured_at": timezone.now().isoformat(),
        },
        format="json",
    )

    assert created.status_code == 201, created.content
    assert client.get(url).json()["count"] == 1

    rewritten = client.put(f"{url}/{created.json()['id']}", {"line_rate": 1.0}, format="json")
    assert rewritten.status_code == 405, "a measurement is a fact, not a record to edit"


def test_a_coverage_snapshot_cannot_reference_another_projects_module() -> None:
    scene = build_scene(Role.QA)
    other = build_scene(Role.QA)
    other_module = Module.objects.get(project=other.project, path_prefix="src/payment")

    response = _client(scene.actor).post(
        _coverage_url(scene),
        {
            "module": str(other_module.pk),
            "line_rate": 0.5,
            "captured_at": timezone.now().isoformat(),
        },
        format="json",
    )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# bug occurrences
# ---------------------------------------------------------------------------
def test_bug_occurrences_drive_the_trajectory_fields() -> None:
    scene = build_scene(Role.QA)
    older = (timezone.now() - timedelta(days=3)).isoformat()
    newer = timezone.now().isoformat()

    response = _client(scene.actor).post(
        scene.urls()["bug_list"],
        {
            "key": "BUG-900",
            "title": "Flaky capture",
            "occurrences": [
                {"seen_at": newer, "count": 2},
                {"seen_at": older, "count": 5},
            ],
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["occurrence_count"] == 7
    assert body["first_seen_at"] < body["last_seen_at"]


def test_a_bug_occurrence_cannot_reference_another_projects_release() -> None:
    scene = build_scene(Role.QA)
    other = build_scene(Role.QA)

    response = _client(scene.actor).post(
        scene.urls()["bug_list"],
        {
            "key": "BUG-901",
            "title": "Cross-tenant attempt",
            "occurrences": [
                {
                    "seen_at": timezone.now().isoformat(),
                    "count": 1,
                    "release": str(other.release.pk),
                }
            ],
        },
        format="json",
    )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# release commit links
# ---------------------------------------------------------------------------
def test_release_commits_are_replaced_by_id() -> None:
    scene = build_scene(Role.PM)
    client = _client(scene.actor)

    created = client.post(
        scene.urls()["release_list"],
        {"version": "2026.10", "commit_ids": [str(scene.commit.pk)]},
        format="json",
    )

    assert created.status_code == 201, created.content
    assert [link["commit"]["sha"] for link in created.json()["commit_links"]] == ["abc123"]

    cleared = client.patch(
        f"{scene.urls()['release_list']}/{created.json()['id']}",
        {"commit_ids": []},
        format="json",
    )

    assert cleared.status_code == 200
    assert cleared.json()["commit_links"] == []


def test_release_commit_ids_must_belong_to_the_project() -> None:
    scene = build_scene(Role.PM)
    other = build_scene(Role.PM)

    response = _client(scene.actor).post(
        scene.urls()["release_list"],
        {"version": "2026.11", "commit_ids": [str(other.commit.pk)]},
        format="json",
    )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# bug relations and guarding cases
# ---------------------------------------------------------------------------
def test_a_bug_can_declare_relations_and_guarding_cases() -> None:
    scene = build_scene(Role.QA)
    duplicate = Bug.objects.create(project=scene.project, key="BUG-2000", title="Same thing")

    response = _client(scene.actor).post(
        scene.urls()["bug_list"],
        {
            "key": "BUG-3000",
            "title": "Root",
            "relations": [{"related_bug": str(duplicate.pk), "kind": "duplicate"}],
            "test_case_ids": [str(scene.test_case.pk)],
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    assert response.json()["relations"][0]["kind"] == "duplicate"
    bug = Bug.objects.get(pk=response.json()["id"])
    assert bug.relations.count() == 1
    assert bug.test_case_links.count() == 1
    # A relation a person typed must be distinguishable from anything inferred.
    assert bug.relations.get().source == LinkSource.MANUAL


def test_a_relation_to_another_projects_bug_is_rejected() -> None:
    scene = build_scene(Role.QA)
    other = build_scene(Role.QA)

    response = _client(scene.actor).post(
        scene.urls()["bug_list"],
        {
            "key": "BUG-3001",
            "title": "x",
            "relations": [{"related_bug": str(other.bug.pk), "kind": "similar"}],
        },
        format="json",
    )

    assert response.status_code == 400


def test_a_bug_cannot_guard_another_projects_test_case() -> None:
    scene = build_scene(Role.QA)
    other = build_scene(Role.QA)

    response = _client(scene.actor).post(
        scene.urls()["bug_list"],
        {"key": "BUG-3002", "title": "x", "test_case_ids": [str(other.test_case.pk)]},
        format="json",
    )

    assert response.status_code == 400


def test_a_passing_result_verifies_the_runs_commit() -> None:
    """The edge the correlation engine reads as "this test exercised this area"."""
    scene = build_scene(Role.QA)

    response = _client(scene.actor).post(
        scene.urls()["test_run_list"],
        {
            "commit": str(scene.commit.pk),
            "results": [
                {"status": "passed", "test_case": str(scene.test_case.pk)},
                {"status": "failed", "test_case": str(scene.test_case.pk)},
            ],
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    link = TestCaseCommitLink.objects.get(test_case=scene.test_case, commit=scene.commit)
    assert link.source == LinkSource.INFERRED
    assert TestCaseCommitLink.objects.count() == 1, "only the passing result verified it"
