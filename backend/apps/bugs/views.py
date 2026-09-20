"""Bug endpoints, nested under a project."""

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
from apps.bugs.models import Bug
from apps.bugs.serializers import BugSerializer

#: Everyone in the delivery team files and triages bugs.
_CAN_WRITE = frozenset({Role.ADMIN, Role.PM, Role.DEVELOPER, Role.QA})


class BugViewSet(ScopedRoleViewMixin, viewsets.ModelViewSet):
    serializer_class = BugSerializer
    permission_classes = (IsAuthenticated, RoleRequired)
    lookup_url_kwarg = "bug_pk"
    filterset_fields = ["status", "severity", "priority", "assignee", "environment"]
    search_fields = ["key", "title", "description", "error_type"]
    ordering_fields = ["created_at", "last_seen_at", "occurrence_count", "severity"]
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
            return Bug.objects.none()
        return (
            Bug.objects.filter(project=project)
            .select_related("reporter", "assignee")
            .prefetch_related("module_links__module", "occurrences")
        )

    def get_serializer_context(self) -> dict[str, Any]:
        context = dict(super().get_serializer_context())
        # Needed by the per-project uniqueness check; see ProjectScopedUniqueMixin.
        context["project"] = self.get_scope_project()
        return context

    def perform_create(self, serializer: Any) -> None:
        serializer.save(project=require_scope_project(self), reporter=self.current_user())
