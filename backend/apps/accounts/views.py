"""Authentication, tenancy and membership endpoints.

Every scoped route carries its organization (and project, when nested) in the
URL. The tenant is never read from the request body, so a caller cannot place a
row into a tenant they do not control.
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

from django.db import transaction
from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts.models import Membership, Organization, Project, ProjectMembership, User
from apps.accounts.permissions import (
    RoleRequired,
    RoleRequirement,
    ScopedRoleViewMixin,
    require_scope_org,
    require_scope_project,
)
from apps.accounts.roles import Role
from apps.accounts.serializers import (
    LoginResponseSerializer,
    LoginSerializer,
    LogoutSerializer,
    OrganizationMemberSerializer,
    OrganizationSerializer,
    ProjectMemberSerializer,
    ProjectSerializer,
    RegisterSerializer,
    UserSerializer,
)
from apps.accounts.services import assert_admin_survives, issue_tokens


class RegisterView(APIView):
    """Create an account, optionally with a new organization the user administers."""

    authentication_classes = ()
    permission_classes = (AllowAny,)
    # Unauthenticated and writes a row per call: the natural target for signup abuse.
    throttle_scope = "register"

    @extend_schema(request=RegisterSerializer, responses={201: LoginResponseSerializer})
    def post(self, request: Request) -> Response:
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user: User = serializer.save()
        return Response(
            {**issue_tokens(user), "user": UserSerializer(user).data},
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    """Exchange email + password for a JWT pair."""

    authentication_classes = ()
    permission_classes = (AllowAny,)
    # Every attempt runs a password hash, so an unthrottled login is both a
    # credential-stuffing surface and a cheap CPU-exhaustion vector.
    throttle_scope = "login"

    @extend_schema(request=LoginSerializer, responses={200: LoginResponseSerializer})
    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return Response(
            {
                "access": data["access"],
                "refresh": data["refresh"],
                "user": UserSerializer(data["user"]).data,
            }
        )


class RefreshView(TokenRefreshView):
    """Exchange a refresh token for a new access token.

    A subclass only to attach a throttle scope: the upstream view is
    unauthenticated, so it needs the same protection as login.
    """

    throttle_scope = "refresh"


class LogoutView(APIView):
    """Invalidate a refresh token so it cannot be exchanged again."""

    permission_classes = (IsAuthenticated,)

    @extend_schema(request=LogoutSerializer, responses={204: None})
    def post(self, request: Request) -> Response:
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(RetrieveUpdateAPIView):
    """Read or update the authenticated user."""

    serializer_class = UserSerializer
    permission_classes = (IsAuthenticated,)

    def get_object(self) -> User:
        return cast(User, self.request.user)


class OrganizationViewSet(ScopedRoleViewMixin, viewsets.ModelViewSet):
    serializer_class = OrganizationSerializer
    permission_classes = (IsAuthenticated, RoleRequired)
    # The organization is addressed as ``org_pk`` because that is the kwarg the
    # nested routes use; without this DRF would look for ``pk`` and 404.
    lookup_url_kwarg = "org_pk"

    required_roles: ClassVar[dict[str, RoleRequirement]] = {
        "list": Role.VIEWER,
        "retrieve": Role.VIEWER,
        "create": Role.VIEWER,  # any authenticated user may start an organization
        "update": Role.ADMIN,
        "partial_update": Role.ADMIN,
        "destroy": Role.ADMIN,
    }

    def get_queryset(self) -> Any:
        user = self.request.user
        # Prefetch the caller's membership so the serializer's `role` field does not
        # issue one query per organization.
        return Organization.objects.accessible_to(user).prefetch_related(
            Prefetch(
                "memberships",
                queryset=Membership.objects.filter(user=user).only("id", "role", "org_id"),
                to_attr="viewer_memberships",
            )
        )

    def perform_create(self, serializer: Any) -> None:
        # Starting an organization makes the creator its first admin; without this
        # the new organization would be unreachable to everybody.
        with transaction.atomic():
            org = serializer.save()
            Membership.objects.create(org=org, user=self.current_user(), role=Role.ADMIN)


class OrganizationMemberViewSet(ScopedRoleViewMixin, viewsets.ModelViewSet):
    serializer_class = OrganizationMemberSerializer
    permission_classes = (IsAuthenticated, RoleRequired)
    lookup_field = "user_id"
    lookup_url_kwarg = "user_id"

    required_roles: ClassVar[dict[str, RoleRequirement]] = {
        "list": Role.VIEWER,
        "retrieve": Role.VIEWER,
        "create": Role.ADMIN,
        "update": Role.ADMIN,
        "partial_update": Role.ADMIN,
        "destroy": Role.ADMIN,
    }

    def get_queryset(self) -> Any:
        org = self.get_scope_org()
        if org is None:
            # No scope in the URL (schema generation, introspection): no rows.
            return Membership.objects.none()
        return Membership.objects.filter(org=org).select_related("user", "org")

    def perform_create(self, serializer: Any) -> None:
        serializer.save(org=require_scope_org(self))

    @transaction.atomic
    def perform_update(self, serializer: Any) -> None:
        """Guard the last admin and write in one transaction.

        The check + write have to share a transaction for the row lock in
        :func:`assert_admin_survives` to mean anything; and it only applies when the
        role is actually being reduced, so an unrelated patch is not blocked.
        """
        instance = serializer.instance
        requested = serializer.validated_data.get("role")
        if (
            requested is not None
            and Role(instance.role) is Role.ADMIN
            and Role(requested) is not Role.ADMIN
        ):
            assert_admin_survives(instance)
        serializer.save()

    @transaction.atomic
    def perform_destroy(self, instance: Membership) -> None:
        assert_admin_survives(instance)
        instance.delete()


class ProjectViewSet(ScopedRoleViewMixin, viewsets.ModelViewSet):
    serializer_class = ProjectSerializer
    permission_classes = (IsAuthenticated, RoleRequired)
    lookup_url_kwarg = "project_pk"

    required_roles: ClassVar[dict[str, RoleRequirement]] = {
        "list": Role.VIEWER,
        "retrieve": Role.VIEWER,
        "create": Role.PM,
        "update": Role.PM,
        "partial_update": Role.PM,
        "destroy": Role.ADMIN,
    }

    def get_queryset(self) -> Any:
        org = self.get_scope_org()
        if org is None:
            # No scope in the URL (schema generation, introspection): no rows.
            return Project.objects.none()
        user = self.request.user
        # `role` on each project is the higher of the org and project roles, so both
        # memberships are prefetched — otherwise every row costs two queries.
        return (
            Project.objects.filter(org=org)
            .select_related("org")
            .prefetch_related(
                Prefetch(
                    "memberships",
                    queryset=ProjectMembership.objects.filter(user=user).only(
                        "id", "role", "project_id"
                    ),
                    to_attr="viewer_project_memberships",
                ),
                Prefetch(
                    "org__memberships",
                    queryset=Membership.objects.filter(user=user).only("id", "role", "org_id"),
                    to_attr="viewer_memberships",
                ),
            )
        )

    def perform_create(self, serializer: Any) -> None:
        serializer.save(org=require_scope_org(self))


class ProjectMemberViewSet(ScopedRoleViewMixin, viewsets.ModelViewSet):
    serializer_class = ProjectMemberSerializer
    permission_classes = (IsAuthenticated, RoleRequired)
    lookup_field = "user_id"
    lookup_url_kwarg = "user_id"

    required_roles: ClassVar[dict[str, RoleRequirement]] = {
        "list": Role.VIEWER,
        "retrieve": Role.VIEWER,
        "create": Role.PM,
        "update": Role.PM,
        "partial_update": Role.PM,
        "destroy": Role.PM,
    }

    def get_queryset(self) -> Any:
        project = self.get_scope_project()
        if project is None:
            # No scope in the URL (schema generation, introspection): no rows.
            return ProjectMembership.objects.none()
        return ProjectMembership.objects.filter(project=project).select_related("user")

    def perform_create(self, serializer: Any) -> None:
        serializer.save(project=require_scope_project(self))
