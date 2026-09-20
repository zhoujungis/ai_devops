"""The AI HTTP surface.

The async shape is deliberate and uniform: starting an analysis returns ``202`` with
a job id, and the client polls. Nothing here waits for a model.

Confirming and rejecting are separate endpoints rather than a status write, because
they are not edits — one runs an executor and one does not, and that difference
deserves to be visible in the URL.
"""

from __future__ import annotations

from typing import Any, ClassVar

from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.permissions import (
    RoleRequired,
    RoleRequirement,
    ScopedRoleViewMixin,
    require_scope_org,
    require_scope_project,
)
from apps.accounts.roles import Role
from apps.ai.agents.base import available_agents
from apps.ai.models import (
    AIAnalysisJob,
    AIFinding,
    AIProviderConfig,
    AIRecommendation,
)
from apps.ai.serializers import (
    AIFindingSerializer,
    AIProviderConfigSerializer,
    AIRecommendationSerializer,
    AnalysisJobSerializer,
    AnalysisRequestSerializer,
    RecommendationDecisionSerializer,
)
from apps.ai.services.confirmation import (
    ConfirmationError,
    execute_recommendation,
    reject_recommendation,
)
from apps.ai.tasks import create_job, run_analysis_job
from apps.core.middleware import get_request_id

#: The delivery team runs analyses; a viewer can read the results but not start or
#: confirm anything.
_CAN_RUN = frozenset({Role.ADMIN, Role.PM, Role.DEVELOPER, Role.QA})


class AnalysisJobViewSet(ScopedRoleViewMixin, viewsets.ReadOnlyModelViewSet):
    """Start an analysis and poll it. The POST returns immediately."""

    serializer_class = AnalysisJobSerializer
    permission_classes = (IsAuthenticated, RoleRequired)
    lookup_url_kwarg = "job_pk"
    filterset_fields = ["status", "agent_code", "target_type"]
    ordering_fields = ["created_at", "finished_at"]
    ordering = ["-created_at"]

    required_roles: ClassVar[dict[str, RoleRequirement]] = {
        "list": Role.VIEWER,
        "retrieve": Role.VIEWER,
        "create": _CAN_RUN,
        "agents": Role.VIEWER,
    }

    def get_queryset(self) -> Any:
        project = self.get_scope_project()
        if project is None:
            return AIAnalysisJob.objects.none()
        return (
            AIAnalysisJob.objects.filter(project=project)
            .select_related("requested_by")
            .prefetch_related("runs", "findings")
        )

    @extend_schema(request=AnalysisRequestSerializer, responses={202: AnalysisJobSerializer})
    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        project = require_scope_project(self)
        payload = AnalysisRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        job, created = create_job(
            project=project,
            agent_code=payload.validated_data["agent"],
            target_type=payload.validated_data.get("target_type", ""),
            target_id=payload.validated_data.get("target_id", ""),
            params=payload.validated_data.get("params") or {},
            requested_by=self.current_user(),
            idempotency_key=payload.validated_data.get("idempotency_key", ""),
        )

        if created:
            task = run_analysis_job.delay(str(job.pk))
            job.celery_task_id = getattr(task, "id", "") or ""
            job.save(update_fields=["celery_task_id", "updated_at"])

        serializer = self.get_serializer(job)
        return Response(serializer.data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(responses={200: None})
    def agents(self, request: Request, **kwargs: Any) -> Response:
        """What can be run, so a client does not hard-code the list."""
        return Response(
            [
                {"code": code, "description": description}
                for code, description in sorted(available_agents().items())
            ]
        )


class AIFindingViewSet(ScopedRoleViewMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = AIFindingSerializer
    permission_classes = (IsAuthenticated, RoleRequired)
    lookup_url_kwarg = "finding_pk"
    filterset_fields = ["severity", "category", "status", "agent_code"]
    ordering_fields = ["created_at", "severity", "confidence"]
    ordering = ["-created_at"]

    required_roles: ClassVar[dict[str, RoleRequirement]] = {
        "list": Role.VIEWER,
        "retrieve": Role.VIEWER,
    }

    def get_queryset(self) -> Any:
        project = self.get_scope_project()
        if project is None:
            return AIFinding.objects.none()
        return AIFinding.objects.filter(project=project)


class AIRecommendationViewSet(ScopedRoleViewMixin, viewsets.ReadOnlyModelViewSet):
    """Proposals awaiting a decision, plus the two decisions themselves."""

    serializer_class = AIRecommendationSerializer
    permission_classes = (IsAuthenticated, RoleRequired)
    lookup_url_kwarg = "recommendation_pk"
    filterset_fields = ["status", "type", "agent_code", "risk_level"]
    ordering = ["-created_at"]

    required_roles: ClassVar[dict[str, RoleRequirement]] = {
        "list": Role.VIEWER,
        "retrieve": Role.VIEWER,
        "confirm": _CAN_RUN,
        "reject": _CAN_RUN,
    }

    def get_queryset(self) -> Any:
        project = self.get_scope_project()
        if project is None:
            return AIRecommendation.objects.none()
        return AIRecommendation.objects.filter(project=project).select_related("confirmation")

    @extend_schema(
        request=RecommendationDecisionSerializer, responses={200: AIRecommendationSerializer}
    )
    def confirm(self, request: Request, **kwargs: Any) -> Response:
        recommendation = self.get_object()
        payload = RecommendationDecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            execute_recommendation(
                recommendation,
                user=self.current_user(),
                edited_payload=payload.validated_data.get("edited_payload"),
                request_id=get_request_id(),
            )
        except ConfirmationError as exc:
            return Response(
                {"error": {"code": "cannot_execute", "message": str(exc), "details": {}}},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(self.get_serializer(recommendation).data)

    @extend_schema(
        request=RecommendationDecisionSerializer, responses={200: AIRecommendationSerializer}
    )
    def reject(self, request: Request, **kwargs: Any) -> Response:
        recommendation = self.get_object()
        payload = RecommendationDecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            reject_recommendation(
                recommendation,
                user=self.current_user(),
                reason=payload.validated_data.get("reason", ""),
                request_id=get_request_id(),
            )
        except ConfirmationError as exc:
            return Response(
                {"error": {"code": "cannot_reject", "message": str(exc), "details": {}}},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(self.get_serializer(recommendation).data)


class AIProviderConfigViewSet(ScopedRoleViewMixin, viewsets.ModelViewSet):
    """Model credentials are organization-scoped and admin-only, like git tokens."""

    serializer_class = AIProviderConfigSerializer
    permission_classes = (IsAuthenticated, RoleRequired)
    lookup_url_kwarg = "provider_pk"

    required_roles: ClassVar[dict[str, RoleRequirement]] = {
        "list": Role.ADMIN,
        "retrieve": Role.ADMIN,
        "create": Role.ADMIN,
        "update": Role.ADMIN,
        "partial_update": Role.ADMIN,
        "destroy": Role.ADMIN,
    }

    def get_queryset(self) -> Any:
        org = self.get_scope_org()
        if org is None:
            return AIProviderConfig.objects.none()
        return AIProviderConfig.objects.filter(org=org)

    def perform_create(self, serializer: Any) -> None:
        serializer.save(org=require_scope_org(self))
