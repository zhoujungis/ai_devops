"""Integration API routes, mounted under ``/api/v1/``."""

from __future__ import annotations

from django.urls import URLPattern, path

from apps.integrations import views

git_connection_list = views.GitConnectionViewSet.as_view({"get": "list", "post": "create"})
git_connection_detail = views.GitConnectionViewSet.as_view(
    {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
)
git_connection_verify = views.GitConnectionViewSet.as_view({"post": "verify"})

repository_list = views.RepositoryViewSet.as_view({"get": "list", "post": "create"})
repository_detail = views.RepositoryViewSet.as_view(
    {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
)
repository_sync = views.RepositoryViewSet.as_view({"post": "sync"})

urlpatterns: list[URLPattern] = [
    path(
        "orgs/<uuid:org_pk>/git-connections",
        git_connection_list,
        name="git-connection-list",
    ),
    path(
        "orgs/<uuid:org_pk>/git-connections/<uuid:connection_pk>",
        git_connection_detail,
        name="git-connection-detail",
    ),
    path(
        "orgs/<uuid:org_pk>/git-connections/<uuid:connection_pk>/verify",
        git_connection_verify,
        name="git-connection-verify",
    ),
    path(
        "orgs/<uuid:org_pk>/projects/<uuid:project_pk>/repositories",
        repository_list,
        name="repository-list",
    ),
    path(
        "orgs/<uuid:org_pk>/projects/<uuid:project_pk>/repositories/<uuid:repository_pk>",
        repository_detail,
        name="repository-detail",
    ),
    path(
        "orgs/<uuid:org_pk>/projects/<uuid:project_pk>/repositories/<uuid:repository_pk>/sync",
        repository_sync,
        name="repository-sync",
    ),
    # Unauthenticated by design: GitHub cannot present a JWT.
    path(
        "webhooks/github/<uuid:connection_pk>",
        views.GitHubWebhookView.as_view(),
        name="github-webhook",
    ),
]
