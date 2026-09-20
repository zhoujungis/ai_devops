"""Requirement API routes, mounted under ``/api/v1/``."""

from __future__ import annotations

from django.urls import URLPattern, path

from apps.requirements import views

requirement_list = views.RequirementViewSet.as_view({"get": "list", "post": "create"})
requirement_detail = views.RequirementViewSet.as_view(
    {
        "get": "retrieve",
        "put": "update",
        "patch": "partial_update",
        "delete": "destroy",
    }
)

urlpatterns: list[URLPattern] = [
    path(
        "orgs/<uuid:org_pk>/projects/<uuid:project_pk>/requirements",
        requirement_list,
        name="requirement-list",
    ),
    path(
        "orgs/<uuid:org_pk>/projects/<uuid:project_pk>/requirements/<uuid:requirement_pk>",
        requirement_detail,
        name="requirement-detail",
    ),
]
