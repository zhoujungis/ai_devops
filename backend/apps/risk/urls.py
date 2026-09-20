"""Risk API routes, mounted under ``/api/v1/``."""

from __future__ import annotations

from django.urls import URLPattern, path

from apps.risk import views

_BASE = "orgs/<uuid:org_pk>/projects/<uuid:project_pk>"

urlpatterns: list[URLPattern] = [
    path(
        f"{_BASE}/commits/<uuid:commit_pk>/risk",
        views.CommitRiskView.as_view(),
        name="commit-risk",
    ),
    path(
        f"{_BASE}/modules/<uuid:module_pk>/risk",
        views.ModuleRiskView.as_view(),
        name="module-risk",
    ),
]
