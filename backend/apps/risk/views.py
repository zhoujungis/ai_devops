"""Risk endpoints.

Read-only and computed live. A risk score is a function of the current data, so
caching it in a response would be a lie the moment anything changed.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import RoleRequired, ScopedRoleViewMixin
from apps.accounts.roles import Role
from apps.codebase.models import Commit, Module
from apps.risk.engine import RiskEngine
from apps.risk.serializers import RiskAssessmentSerializer


class _RiskView(ScopedRoleViewMixin, APIView):
    """Read-only, and gated at the baseline: risk is visible to every member."""

    permission_classes = (IsAuthenticated, RoleRequired)

    def required_role_for_action(self) -> Any:
        # APIView has no `action`, so the requirement is stated directly rather than
        # read from a per-action table.
        return Role.VIEWER


class CommitRiskView(_RiskView):
    @extend_schema(responses={200: RiskAssessmentSerializer, 404: None})
    def get(self, request: Request, **kwargs: Any) -> Response:
        commit = (
            Commit.objects.filter(repository__project=self.get_scope_project())
            .filter(pk=self.kwargs["commit_pk"])
            .select_related("repository", "repository__project")
            .first()
        )
        if commit is None:
            # Raised, not returned: the global handler is what attaches the request id
            # and the stable code, and a hand-built body would silently skip both.
            raise NotFound("No such commit in this project.")

        assessment = RiskEngine().assess_commit(commit)
        return Response(RiskAssessmentSerializer(assessment).data)


class ModuleRiskView(_RiskView):
    @extend_schema(responses={200: RiskAssessmentSerializer, 404: None})
    def get(self, request: Request, **kwargs: Any) -> Response:
        module = Module.objects.filter(
            project=self.get_scope_project(), pk=self.kwargs["module_pk"]
        ).first()
        if module is None:
            raise NotFound("No such module in this project.")

        assessment = RiskEngine().assess_module(module)
        return Response(RiskAssessmentSerializer(assessment).data)
