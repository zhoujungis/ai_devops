"""Release endpoints, nested under a project."""

from __future__ import annotations

from typing import Any, ClassVar

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import (
    RoleRequired,
    RoleRequirement,
    ScopedRoleViewMixin,
    require_scope_project,
)
from apps.accounts.roles import Role
from apps.releases.models import Release
from apps.releases.serializers import ReleaseSerializer

#: Release management belongs to product and admins.
_CAN_WRITE = frozenset({Role.ADMIN, Role.PM})


class ReleaseViewSet(ScopedRoleViewMixin, viewsets.ModelViewSet):
    serializer_class = ReleaseSerializer
    permission_classes = (IsAuthenticated, RoleRequired)
    lookup_url_kwarg = "release_pk"
    filterset_fields = ["status", "version"]
    search_fields = ["version", "name", "notes"]
    ordering_fields = ["created_at", "released_at", "planned_at", "version"]
    ordering = ["-created_at"]

    required_roles: ClassVar[dict[str, RoleRequirement]] = {
        "list": Role.VIEWER,
        "retrieve": Role.VIEWER,
        "create": _CAN_WRITE,
        "update": _CAN_WRITE,
        "partial_update": _CAN_WRITE,
        "destroy": _CAN_WRITE,
    }

    def get_queryset(self) -> Any:
        project = self.get_scope_project()
        if project is None:
            return Release.objects.none()
        return Release.objects.filter(project=project).prefetch_related(
            "commit_links__commit", "risk_snapshots"
        )

    def get_serializer_context(self) -> dict[str, Any]:
        context = dict(super().get_serializer_context())
        # Needed by the per-project uniqueness check; see ProjectScopedUniqueMixin.
        context["project"] = self.get_scope_project()
        return context

    def perform_create(self, serializer: Any) -> None:
        serializer.save(project=require_scope_project(self), created_by=self.current_user())
