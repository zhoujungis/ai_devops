"""Bug API routes, mounted under ``/api/v1/``."""

from __future__ import annotations

from django.urls import URLPattern, path

from apps.bugs import views

bug_list = views.BugViewSet.as_view({"get": "list", "post": "create"})
bug_detail = views.BugViewSet.as_view(
    {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
)

_BASE = "orgs/<uuid:org_pk>/projects/<uuid:project_pk>"

urlpatterns: list[URLPattern] = [
    path(f"{_BASE}/bugs", bug_list, name="bug-list"),
    path(f"{_BASE}/bugs/<uuid:bug_pk>", bug_detail, name="bug-detail"),
]
