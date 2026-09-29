"""The AI HTTP surface.

The async shape is deliberate and uniform: starting an analysis returns ``202`` with
a job id, and the client polls. Nothing here waits for a model.

Confirming and rejecting are separate endpoints rather than a status write, because
they are not edits — one runs an executor and one does not, and that difference
deserves to be visible in the URL.
"""

from __future__ import annotations

from typing import Any, ClassVar

from django.utils import timezone
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
    AuditLog,
    ProviderStatus,
)
from apps.ai.providers.registry import build_provider
from apps.ai.serializers import (
    AIFindingSerializer,
    AIProviderConfigSerializer,
    AIRecommendationSerializer,
    AnalysisJobSerializer,
    AnalysisRequestSerializer,
    AnalysisRunTraceSerializer,
    AuditLogSerializer,
    FindingStatusSerializer,
    RecommendationDecisionSerializer,
)
from apps.ai.services.audit import record_human_action
from apps.ai.services.confirmation import (
    ConfirmationError,
    execute_recommendation,
    reject_recommendation,
)
from apps.ai.tasks import create_job, run_analysis_job
from apps.core.exceptions import ApplicationError
from apps.core.middleware import get_request_id

#: The delivery team runs analyses; a viewer can read the results but not start or
#: confirm anything.
_CAN_RUN = frozenset({Role.ADMIN, Role.PM, Role.DEVELOPER, Role.QA})


class RecommendationConflictError(ApplicationError):
    """The proposal is no longer pending, so it cannot be actioned.

    Raised rather than returned as a hand-built body so it flows through the single
    exception handler — which is what attaches the request id and the stable code.
    """

    status_code: int = status.HTTP_409_CONFLICT
    default_code: str = "conflict"


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
        "trace": Role.VIEWER,
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

    @extend_schema(responses={200: AnalysisRunTraceSerializer(many=True)})
    def trace(self, request: Request, **kwargs: Any) -> Response:
        """Every model interaction and tool call behind one job.

        The job endpoint reports the outcome; this reports how it was reached, which is
        what a reviewer needs to check an answer rather than take it on faith.
        """
        job = self.get_object()
        runs = job.runs.prefetch_related("tool_calls").order_by("sequence")
        return Response(AnalysisRunTraceSerializer(runs, many=True).data)


class AIFindingViewSet(ScopedRoleViewMixin, viewsets.ReadOnlyModelViewSet):
    """Findings are the model's output. Its *status* is the reader's."""

    serializer_class = AIFindingSerializer
    permission_classes = (IsAuthenticated, RoleRequired)
    lookup_url_kwarg = "finding_pk"
    filterset_fields = ["severity", "category", "status", "agent_code"]
    ordering_fields = ["created_at", "severity", "confidence"]
    ordering = ["-created_at"]
    #: `severity` is a text column, so without this "worst first" would sort
    #: alphabetically and put `critical` before `high` before `info`.
    ranked_ordering: ClassVar[dict[str, dict[str, int]]] = {
        "severity": {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4},
    }

    required_roles: ClassVar[dict[str, RoleRequirement]] = {
        "list": Role.VIEWER,
        "retrieve": Role.VIEWER,
        "set_status": _CAN_RUN,
    }

    def get_queryset(self) -> Any:
        project = self.get_scope_project()
        if project is None:
            return AIFinding.objects.none()
        return AIFinding.objects.filter(project=project)

    @extend_schema(request=FindingStatusSerializer, responses={200: AIFindingSerializer})
    def set_status(self, request: Request, **kwargs: Any) -> Response:
        """Triage a finding: acknowledge or dismiss it, or mark it converted.

        The content of a finding is never editable — only the human judgement about what
        to do with it — and the decision is written to the audit log like every other
        state change.
        """
        finding = self.get_object()
        payload = FindingStatusSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        previous = finding.status
        requested = payload.validated_data["status"]
        if requested != previous:
            finding.status = requested
            finding.save(update_fields=["status", "updated_at"])
            record_human_action(
                org=finding.project.org,
                user=self.current_user(),
                action="ai_finding.status_changed",
                target_type="ai_finding",
                target_id=str(finding.pk),
                before={"status": previous},
                after={"status": finding.status},
                request_id=get_request_id(),
            )
        return Response(self.get_serializer(finding).data)


class AIRecommendationViewSet(ScopedRoleViewMixin, viewsets.ReadOnlyModelViewSet):
    """Proposals awaiting a decision, plus the two decisions themselves."""

    serializer_class = AIRecommendationSerializer
    permission_classes = (IsAuthenticated, RoleRequired)
    lookup_url_kwarg = "recommendation_pk"
    filterset_fields = ["status", "type", "agent_code", "risk_level"]
    ordering_fields = ["created_at", "risk_level"]
    ordering = ["-created_at"]
    #: `risk_level` is stored as free text, so "most severe first" needs a rank too.
    ranked_ordering: ClassVar[dict[str, dict[str, int]]] = {
        "risk_level": {"low": 0, "medium": 1, "high": 2, "critical": 3},
    }

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
            raise RecommendationConflictError(detail=str(exc), code="cannot_execute") from exc
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
            raise RecommendationConflictError(detail=str(exc), code="cannot_reject") from exc
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
        "verify": Role.ADMIN,
    }

    def get_queryset(self) -> Any:
        org = self.get_scope_org()
        if org is None:
            return AIProviderConfig.objects.none()
        return AIProviderConfig.objects.filter(org=org)

    def perform_create(self, serializer: Any) -> None:
        serializer.save(org=require_scope_org(self))

    @extend_schema(request=None, responses=None)
    def verify(self, request: Request, **kwargs: Any) -> Response:
        """Check the stored credentials against the endpoint, and record the verdict.

        A revoked key should be found here — once, deliberately — rather than on the
        next analysis, where it would surface as a failed job and look like a model
        problem instead of a credential one.
        """
        config = self.get_object()
        provider = build_provider(config)
        try:
            verified = provider.verify()
        finally:
            provider.close()

        config.status = ProviderStatus.ACTIVE if verified else ProviderStatus.INVALID
        config.last_verified_at = timezone.now()
        config.save(update_fields=["status", "last_verified_at", "updated_at"])
        return Response(
            {
                "verified": verified,
                "status": config.status,
                "checked_at": config.last_verified_at,
            }
        )


class AuditLogViewSet(ScopedRoleViewMixin, viewsets.ReadOnlyModelViewSet):
    """The audit trail, organization-scoped and admin-only.

    Read-only by construction: an audit log you can write through the API is not an
    audit log. It exists so "who confirmed this, and when" is answerable without a
    database shell.
    """

    serializer_class = AuditLogSerializer
    permission_classes = (IsAuthenticated, RoleRequired)
    lookup_url_kwarg = "audit_pk"
    filterset_fields = ["actor_type", "action", "target_type", "target_id"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    required_roles: ClassVar[dict[str, RoleRequirement]] = {
        "list": Role.ADMIN,
        "retrieve": Role.ADMIN,
    }

    def get_queryset(self) -> Any:
        org = self.get_scope_org()
        if org is None:
            return AuditLog.objects.none()
        return AuditLog.objects.filter(org=org)
