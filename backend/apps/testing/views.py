"""Test case, suite and run endpoints, nested under a project."""

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
from apps.testing.models import TestCase, TestRun, TestSuite
from apps.testing.serializers import (
    TestCaseSerializer,
    TestRunSerializer,
    TestSuiteSerializer,
)

#: QA and developers own the test estate; PM does not edit cases.
_CAN_WRITE = frozenset({Role.ADMIN, Role.DEVELOPER, Role.QA})


class _ProjectScopedViewSet(ScopedRoleViewMixin, viewsets.ModelViewSet):
    permission_classes = (IsAuthenticated, RoleRequired)

    required_roles: ClassVar[dict[str, RoleRequirement]] = {
        "list": Role.VIEWER,
        "retrieve": Role.VIEWER,
        "create": _CAN_WRITE,
        "update": _CAN_WRITE,
        "partial_update": _CAN_WRITE,
        "destroy": _CAN_WRITE,
    }

    model: Any = None

    def get_queryset(self) -> Any:
        project = self.get_scope_project()
        if project is None:
            return self.model.objects.none()
        return self.model.objects.filter(project=project)

    def get_serializer_context(self) -> dict[str, Any]:
        context = dict(super().get_serializer_context())
        # Needed by the per-project uniqueness check; see ProjectScopedUniqueMixin.
        context["project"] = self.get_scope_project()
        return context


class TestCaseViewSet(_ProjectScopedViewSet):
    serializer_class = TestCaseSerializer
    lookup_url_kwarg = "test_case_pk"
    model = TestCase
    filterset_fields = ["status", "priority", "automation", "origin"]
    search_fields = ["key", "title", "expected"]
    ordering_fields = ["key", "priority", "created_at"]
    ordering = ["key"]

    def get_queryset(self) -> Any:
        return super().get_queryset().prefetch_related("steps", "module_links__module")

    def perform_create(self, serializer: Any) -> None:
        serializer.save(project=require_scope_project(self), created_by=self.current_user())


class TestSuiteViewSet(_ProjectScopedViewSet):
    serializer_class = TestSuiteSerializer
    lookup_url_kwarg = "test_suite_pk"
    model = TestSuite
    search_fields = ["name", "description"]
    ordering = ["name"]


class TestRunViewSet(_ProjectScopedViewSet):
    serializer_class = TestRunSerializer
    lookup_url_kwarg = "test_run_pk"
    model = TestRun
    filterset_fields = ["status", "trigger", "environment", "suite"]
    ordering_fields = ["created_at", "started_at", "failed"]
    ordering = ["-created_at"]

    def get_queryset(self) -> Any:
        return super().get_queryset().select_related("suite", "commit")

    def perform_create(self, serializer: Any) -> None:
        serializer.save(project=require_scope_project(self))
