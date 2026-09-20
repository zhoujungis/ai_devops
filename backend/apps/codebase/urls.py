"""Code entity API routes, mounted under ``/api/v1/``."""

from __future__ import annotations

from django.urls import URLPattern, path

from apps.codebase import views

commit_list = views.CommitViewSet.as_view({"get": "list"})
commit_detail = views.CommitViewSet.as_view({"get": "retrieve"})
commit_explain = views.CommitViewSet.as_view({"get": "explain"})
module_list = views.ModuleViewSet.as_view({"get": "list"})
module_detail = views.ModuleViewSet.as_view({"get": "retrieve"})

urlpatterns: list[URLPattern] = [
    path(
        "orgs/<uuid:org_pk>/projects/<uuid:project_pk>/commits",
        commit_list,
        name="commit-list",
    ),
    path(
        "orgs/<uuid:org_pk>/projects/<uuid:project_pk>/commits/<uuid:commit_pk>",
        commit_detail,
        name="commit-detail",
    ),
    path(
        "orgs/<uuid:org_pk>/projects/<uuid:project_pk>/commits/<uuid:commit_pk>/explain",
        commit_explain,
        name="commit-explain",
    ),
    path(
        "orgs/<uuid:org_pk>/projects/<uuid:project_pk>/modules",
        module_list,
        name="module-list",
    ),
    path(
        "orgs/<uuid:org_pk>/projects/<uuid:project_pk>/modules/<uuid:module_pk>",
        module_detail,
        name="module-detail",
    ),
]
