"""Requirement endpoints, nested under a project."""

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
from apps.requirements.models import Requirement
from apps.requirements.serializers import RequirementSerializer

#: PM, developers and admins define requirements; QA consumes them.
_CAN_WRITE = frozenset({Role.ADMIN, Role.PM, Role.DEVELOPER})


class RequirementViewSet(ScopedRoleViewMixin, viewsets.ModelViewSet):
    serializer_class = RequirementSerializer
    permission_classes = (IsAuthenticated, RoleRequired)
    lookup_url_kwarg = "requirement_pk"
    filterset_fields = ["status", "priority", "source", "sprint"]
    search_fields = ["external_key", "title", "description"]
    ordering_fields = ["external_key", "priority", "created_at", "status"]
    ordering = ["external_key"]

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
            return Requirement.objects.none()
        return Requirement.objects.filter(project=project).prefetch_related(
            "items", "module_links__module"
        )

    def get_serializer_context(self) -> dict[str, Any]:
        context = dict(super().get_serializer_context())
        # The uniqueness validator needs the owning project, which comes from the
        # URL rather than the body. None during schema generation; the database
        # constraint remains the final guarantee either way.
        context["project"] = self.get_scope_project()
        return context

    def perform_create(self, serializer: Any) -> None:
        serializer.save(project=require_scope_project(self), created_by=self.current_user())
