"""Git integration endpoints.

Credentials are admin-only; read access to synced code is not. The webhook endpoint
is the one unauthenticated route, and its authenticity comes from the HMAC
signature rather than a token.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import (
    RoleRequired,
    RoleRequirement,
    ScopedRoleViewMixin,
    require_scope_org,
    require_scope_project,
)
from apps.accounts.roles import Role
from apps.core.exceptions import ApplicationError
from apps.integrations.git.factory import provider_for
from apps.integrations.models import (
    ConnectionStatus,
    GitConnection,
    Repository,
    SyncStatus,
    WebhookEvent,
)
from apps.integrations.serializers import (
    GitConnectionSerializer,
    RepositoryCreateSerializer,
    RepositorySerializer,
    WebhookAcceptedSerializer,
)
from apps.integrations.services import connect_repository
from apps.integrations.tasks import process_webhook_event_task, sync_repository_task
from apps.integrations.webhooks import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    verify_signature,
)

logger = logging.getLogger(__name__)


class ConnectionInUseError(ApplicationError):
    """Raised when a connection is deleted while repositories still use it."""

    status_code: int = status.HTTP_409_CONFLICT
    default_detail: str = "This connection still has repositories attached."
    default_code: str = "connection_in_use"


class WebhookSignatureError(ApplicationError):
    """Raised when a delivery's HMAC does not match.

    Deliberately not ``AuthenticationFailed``: DRF coerces that to 403 when the
    view has no authenticator able to emit ``WWW-Authenticate``, and a rejected
    webhook is an authentication failure, so it must stay a 401.
    """

    status_code: int = status.HTTP_401_UNAUTHORIZED
    default_detail: str = "Invalid webhook signature."
    default_code: str = "invalid_signature"


class SyncInProgressError(ApplicationError):
    """A sync for this repository is already running."""

    status_code: int = status.HTTP_409_CONFLICT
    default_detail: str = "A sync is already running for this repository."
    default_code: str = "sync_in_progress"


class GitConnectionViewSet(ScopedRoleViewMixin, viewsets.ModelViewSet):
    """Credentials are organization-scoped and admin-only."""

    serializer_class = GitConnectionSerializer
    permission_classes = (IsAuthenticated, RoleRequired)
    lookup_url_kwarg = "connection_pk"

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
            return GitConnection.objects.none()
        return GitConnection.objects.filter(org=org).select_related("owner")

    def perform_create(self, serializer: Any) -> None:
        serializer.save(org=require_scope_org(self), owner=self.current_user())

    def perform_destroy(self, instance: GitConnection) -> None:
        try:
            instance.delete()
        except ProtectedError as exc:
            raise ConnectionInUseError() from exc

    def verify(self, request: Request, **kwargs: Any) -> Response:
        """Check the stored credentials against the provider."""
        connection = self.get_object()
        provider = provider_for(connection)
        try:
            verified = provider.verify()
        finally:
            provider.close()

        connection.status = ConnectionStatus.ACTIVE if verified else ConnectionStatus.INVALID
        connection.last_verified_at = timezone.now()
        connection.save(update_fields=["status", "last_verified_at", "updated_at"])
        return Response(
            {
                "verified": verified,
                "status": connection.status,
                "checked_at": connection.last_verified_at,
            }
        )


class RepositoryViewSet(ScopedRoleViewMixin, viewsets.ModelViewSet):
    serializer_class = RepositorySerializer
    permission_classes = (IsAuthenticated, RoleRequired)
    lookup_url_kwarg = "repository_pk"

    required_roles: ClassVar[dict[str, RoleRequirement]] = {
        "list": Role.VIEWER,
        "retrieve": Role.VIEWER,
        "create": Role.PM,
        "update": Role.PM,
        "partial_update": Role.PM,
        "destroy": Role.ADMIN,
        "sync": Role.DEVELOPER,
    }

    def get_serializer_class(self) -> Any:
        if self.action == "create":
            return RepositoryCreateSerializer
        return RepositorySerializer

    def get_queryset(self) -> Any:
        project = self.get_scope_project()
        if project is None:
            return Repository.objects.none()
        return Repository.objects.filter(project=project).select_related("connection", "project")

    def get_serializer_context(self) -> dict[str, Any]:
        context = dict(super().get_serializer_context())
        # None during schema generation; the create path resolves it again and 404s
        # before anything is written.
        context["project"] = self.get_scope_project()
        return context

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        project = require_scope_project(self)
        serializer = self.get_serializer_class()(
            data=request.data, context={**self.get_serializer_context(), "project": project}
        )
        serializer.is_valid(raise_exception=True)

        repository = connect_repository(
            project=project,
            connection=serializer.validated_data["connection"],
            full_name=serializer.validated_data["full_name"],
            sync_window_days=serializer.validated_data.get("sync_window_days"),
            module_depth=serializer.validated_data.get("module_depth"),
            module_overrides=serializer.validated_data.get("module_overrides"),
        )
        return Response(
            RepositorySerializer(repository, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    def sync(self, request: Request, **kwargs: Any) -> Response:
        """Queue a synchronisation run and return immediately.

        Refuses while one is already running. Two concurrent runs write the same
        cursor, status and ``last_synced_at``, and last-writer-wins silently loses
        progress. This is a guard rather than a lock — the task itself is what marks the
        repository RUNNING — so it closes the common cases (a double submit, an
        impatient retry) without holding a lock across the sync's network calls.
        """
        repository = self.get_object()
        if repository.sync_status == SyncStatus.RUNNING:
            raise SyncInProgressError()
        task = sync_repository_task.delay(str(repository.pk))
        return Response(
            {"status": "queued", "repository": str(repository.pk), "task_id": task.id},
            status=status.HTTP_202_ACCEPTED,
        )


class GitHubWebhookView(APIView):
    """Receives GitHub deliveries.

    Authenticity comes from the HMAC signature, so no token is required. The
    delivery id is unique in the database, which makes a re-delivery a no-op.
    """

    authentication_classes = ()
    permission_classes = (AllowAny,)
    # Unauthenticated and writes a row plus queues work per call, so it is throttled
    # even though the HMAC already proves the delivery came from the configured host.
    throttle_scope = "webhook"

    @extend_schema(
        summary="Receive a GitHub delivery",
        description=(
            "Authenticated by the HMAC signature over the raw body, not by a token. "
            "Re-delivering the same delivery id is a no-op."
        ),
        request=None,
        responses={202: WebhookAcceptedSerializer},
    )
    def post(self, request: Request, connection_pk: Any) -> Response:
        connection = GitConnection.objects.filter(pk=connection_pk).first()
        if connection is None:
            raise NotFound("Unknown connection.")

        signature = request.headers.get(SIGNATURE_HEADER, "")
        if not verify_signature(
            secret=connection.webhook_secret, body=request.body, signature_header=signature
        ):
            logger.warning("Rejected webhook for connection %s: signature mismatch", connection.pk)
            raise WebhookSignatureError()

        delivery_id = request.headers.get(DELIVERY_HEADER, "").strip()
        if not delivery_id:
            raise ValidationError({DELIVERY_HEADER: "Missing delivery id."})

        event, created = WebhookEvent.objects.get_or_create(
            delivery_id=delivery_id,
            defaults={
                "connection": connection,
                "event_type": request.headers.get(EVENT_HEADER, "unknown"),
                "payload": request.data,
            },
        )
        if not created:
            return Response({"status": "duplicate"}, status=status.HTTP_202_ACCEPTED)

        # Enqueue only once the delivery row is actually committed, so a worker can
        # never pick up an event that a rolled-back transaction removed.
        transaction.on_commit(lambda: process_webhook_event_task.delay(str(event.pk)))
        return Response(
            {"status": "accepted", "event_id": str(event.pk)},
            status=status.HTTP_202_ACCEPTED,
        )
