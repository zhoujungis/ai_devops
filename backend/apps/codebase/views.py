"""Read-only views over synced code, plus the correlation chain."""

from __future__ import annotations

from typing import Any

import django_filters
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.permissions import RoleRequired, ScopedRoleViewMixin
from apps.accounts.roles import Role
from apps.codebase.models import Commit, Module
from apps.codebase.serializers import (
    CommitDetailSerializer,
    CommitExplanationSerializer,
    CommitSerializer,
    ModuleSerializer,
)
from services.correlation import CorrelationService


class CommitFilter(django_filters.FilterSet):
    since = django_filters.IsoDateTimeFilter(field_name="committed_at", lookup_expr="gte")
    until = django_filters.IsoDateTimeFilter(field_name="committed_at", lookup_expr="lte")

    class Meta:
        model = Commit
        fields = ["repository", "author_email"]


class CommitViewSet(ScopedRoleViewMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = (IsAuthenticated, RoleRequired)
    lookup_url_kwarg = "commit_pk"
    filterset_class = CommitFilter
    search_fields = ["message", "sha", "author_name", "author_email"]
    ordering_fields = ["committed_at", "additions", "deletions", "files_changed"]
    ordering = ["-committed_at"]

    required_roles = {
        "list": Role.VIEWER,
        "retrieve": Role.VIEWER,
        "explain": Role.VIEWER,
    }

    def get_serializer_class(self) -> Any:
        return CommitDetailSerializer if self.action == "retrieve" else CommitSerializer

    def explain(self, request: Request, **kwargs: Any) -> Response:
        """Walk the correlation chain for one commit.

        Exposed as its own endpoint because it is the product's core question —
        "what does this change touch and imply" — not a field of the commit.
        """
        commit = self.get_object()
        explanation = CorrelationService().explain_commit(commit)
        return Response(CommitExplanationSerializer(explanation).data)

    def get_queryset(self) -> Any:
        project = self.get_scope_project()
        if project is None:
            return Commit.objects.none()
        return (
            Commit.objects.filter(repository__project=project)
            .select_related("repository")
            .prefetch_related("module_impacts__module", "files")
        )


class ModuleViewSet(ScopedRoleViewMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = ModuleSerializer
    permission_classes = (IsAuthenticated, RoleRequired)
    lookup_url_kwarg = "module_pk"
    filterset_fields = ["kind", "language"]
    search_fields = ["name", "path_prefix"]
    ordering_fields = ["name", "path_prefix", "centrality_score"]
    ordering = ["path_prefix"]

    required_roles = {"list": Role.VIEWER, "retrieve": Role.VIEWER}

    def get_queryset(self) -> Any:
        project = self.get_scope_project()
        if project is None:
            return Module.objects.none()
        return Module.objects.filter(project=project)
