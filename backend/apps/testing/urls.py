"""Testing API routes, mounted under ``/api/v1/``."""

from __future__ import annotations

from django.urls import URLPattern, path

from apps.testing import views

test_case_list = views.TestCaseViewSet.as_view({"get": "list", "post": "create"})
test_case_detail = views.TestCaseViewSet.as_view(
    {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
)
test_suite_list = views.TestSuiteViewSet.as_view({"get": "list", "post": "create"})
test_suite_detail = views.TestSuiteViewSet.as_view(
    {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
)
test_run_list = views.TestRunViewSet.as_view({"get": "list", "post": "create"})
test_run_detail = views.TestRunViewSet.as_view(
    {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
)
coverage_list = views.CoverageSnapshotViewSet.as_view({"get": "list", "post": "create"})
coverage_detail = views.CoverageSnapshotViewSet.as_view({"get": "retrieve"})

_BASE = "orgs/<uuid:org_pk>/projects/<uuid:project_pk>"

urlpatterns: list[URLPattern] = [
    path(f"{_BASE}/test-cases", test_case_list, name="test-case-list"),
    path(
        f"{_BASE}/test-cases/<uuid:test_case_pk>",
        test_case_detail,
        name="test-case-detail",
    ),
    path(f"{_BASE}/test-suites", test_suite_list, name="test-suite-list"),
    path(
        f"{_BASE}/test-suites/<uuid:test_suite_pk>",
        test_suite_detail,
        name="test-suite-detail",
    ),
    path(f"{_BASE}/test-runs", test_run_list, name="test-run-list"),
    path(f"{_BASE}/test-runs/<uuid:test_run_pk>", test_run_detail, name="test-run-detail"),
    path(f"{_BASE}/coverage-snapshots", coverage_list, name="coverage-snapshot-list"),
    path(
        f"{_BASE}/coverage-snapshots/<uuid:snapshot_pk>",
        coverage_detail,
        name="coverage-snapshot-detail",
    ),
]
