"""Release API routes, mounted under ``/api/v1/``."""

from __future__ import annotations

from django.urls import URLPattern, path

from apps.releases import views

release_list = views.ReleaseViewSet.as_view({"get": "list", "post": "create"})
release_detail = views.ReleaseViewSet.as_view(
    {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
)

_BASE = "orgs/<uuid:org_pk>/projects/<uuid:project_pk>"

urlpatterns: list[URLPattern] = [
    path(f"{_BASE}/releases", release_list, name="release-list"),
    path(f"{_BASE}/releases/<uuid:release_pk>", release_detail, name="release-detail"),
]
