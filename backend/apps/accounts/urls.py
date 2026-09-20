"""Accounts API routes, mounted under ``/api/v1/``."""

from __future__ import annotations

from django.urls import URLPattern, path
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts import views

organization_list = views.OrganizationViewSet.as_view({"get": "list", "post": "create"})
organization_detail = views.OrganizationViewSet.as_view(
    {
        "get": "retrieve",
        "put": "update",
        "patch": "partial_update",
        "delete": "destroy",
    }
)
organization_member_list = views.OrganizationMemberViewSet.as_view(
    {"get": "list", "post": "create"}
)
organization_member_detail = views.OrganizationMemberViewSet.as_view(
    {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
)
project_list = views.ProjectViewSet.as_view({"get": "list", "post": "create"})
project_detail = views.ProjectViewSet.as_view(
    {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
)
project_member_list = views.ProjectMemberViewSet.as_view({"get": "list", "post": "create"})
project_member_detail = views.ProjectMemberViewSet.as_view(
    {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
)

urlpatterns: list[URLPattern] = [
    path("auth/register", views.RegisterView.as_view(), name="auth-register"),
    path("auth/login", views.LoginView.as_view(), name="auth-login"),
    path("auth/refresh", TokenRefreshView.as_view(), name="auth-refresh"),
    path("auth/logout", views.LogoutView.as_view(), name="auth-logout"),
    path("me", views.MeView.as_view(), name="me"),
    path("orgs", organization_list, name="organization-list"),
    path("orgs/<uuid:org_pk>", organization_detail, name="organization-detail"),
    path("orgs/<uuid:org_pk>/members", organization_member_list, name="organization-member-list"),
    path(
        "orgs/<uuid:org_pk>/members/<uuid:user_id>",
        organization_member_detail,
        name="organization-member-detail",
    ),
    path("orgs/<uuid:org_pk>/projects", project_list, name="project-list"),
    path(
        "orgs/<uuid:org_pk>/projects/<uuid:project_pk>",
        project_detail,
        name="project-detail",
    ),
    path(
        "orgs/<uuid:org_pk>/projects/<uuid:project_pk>/members",
        project_member_list,
        name="project-member-list",
    ),
    path(
        "orgs/<uuid:org_pk>/projects/<uuid:project_pk>/members/<uuid:user_id>",
        project_member_detail,
        name="project-member-detail",
    ),
]
