"""Authentication, tenancy and membership endpoints.

Every scoped route carries its organization (and project, when nested) in the
URL. The tenant is never read from the request body, so a caller cannot place a
row into a tenant they do not control.
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

from django.conf import settings as django_settings
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts import github
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
    GitHubConfigSerializer,
    GitHubLoginSerializer,
    LoginResponseSerializer,
    LogoutSerializer,
    OrganizationMemberSerializer,
    OrganizationSerializer,
    ProjectMemberSerializer,
    ProjectSerializer,
    UserSerializer,
)
from apps.accounts.services import assert_admin_survives, issue_tokens


class GitHubUnavailable(APIException):
    """GitHub could not be reached while verifying a token.

    DRF ships no 5xx client-facing exception, so this is the one place that turns an
    upstream outage into a stable, documented error instead of a bare 500.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "GitHub could not be reached. Try again."
    default_code = "github_unavailable"


class InvalidGitHubToken(APIException):
    """The token GitHub was handed is not valid.

    Deliberately not ``AuthenticationFailed``: this view declares no authenticators,
    so DRF would rewrite that to a 403 ("no WWW-Authenticate header"). A bare 401
    APIException keeps the status the contract promises.
    """

    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "The GitHub access token is invalid."
    default_code = "invalid_github_token"


class GitHubLoginView(APIView):
    """Exchange a GitHub access token for a JWT pair, creating the user on first use.

    The token comes from the device flow the CLI runs directly against GitHub.
    Identity is matched on the immutable numeric ``github_id``; a rename on GitHub
    and a private email both leave the account intact.
    """

    authentication_classes = ()
    permission_classes = (AllowAny,)
    # Unauthenticated and every call reaches out to GitHub: the natural target for
    # abuse, and the reason the old login throttle moved here with the endpoint.
    throttle_scope = "github"

    @extend_schema(request=GitHubLoginSerializer, responses={200: LoginResponseSerializer})
    def post(self, request: Request) -> Response:
        serializer = GitHubLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        access_token = str(serializer.validated_data["access_token"])

        identity = self._resolve(access_token)
        user = self._get_or_create(identity)

        if not user.is_active:
            # The identity is verified; the account is simply not allowed in. 403,
            # not 401, so a deactivated user is not told their token was wrong.
            raise PermissionDenied("This account is disabled.")

        # This view issues JWTs rather than going through django.contrib.auth.login, so
        # the user_logged_in signal never fires and last_login would stay null forever.
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])

        return Response({**issue_tokens(user), "user": UserSerializer(user).data})

    @staticmethod
    def _resolve(access_token: str) -> github.GitHubIdentity:
        try:
            return github.identity_from_token(access_token)
        except github.GitHubAuthError as exc:
            # A token GitHub refuses is a 401 for the caller, never a 500.
            raise InvalidGitHubToken() from exc
        except github.GitHubError as exc:
            raise GitHubUnavailable() from exc

    @staticmethod
    def _get_or_create(identity: github.GitHubIdentity) -> User:
        user = User.objects.filter(github_id=identity.github_id).first()
        if user is None:
            try:
                # The savepoint keeps the connection usable if the insert is rejected,
                # so the recovery lookup below runs even under ATOMIC_REQUESTS.
                with transaction.atomic():
                    return User.objects.create_from_github(
                        github_id=identity.github_id,
                        login=identity.login,
                        email=GitHubLoginView._unique_email(identity),
                        avatar_url=identity.avatar_url,
                    )
            except IntegrityError:
                # Two first logins raced: the other request inserted this github_id
                # between our lookup and our insert. Adopt the row it created rather
                # than turning a perfectly good login into a 500.
                user = User.objects.filter(github_id=identity.github_id).first()
                if user is None:
                    raise

        # Refresh the fields GitHub owns and that can change under us. Email is left
        # alone: it is still the USERNAME_FIELD and is what memberships reference.
        changed = False
        if user.github_login != identity.login:
            user.github_login = identity.login
            changed = True
        if identity.avatar_url and user.avatar_url != identity.avatar_url:
            user.avatar_url = identity.avatar_url
            changed = True
        if changed:
            user.save(update_fields=["github_login", "avatar_url"])
        return user

    @staticmethod
    def _unique_email(identity: github.GitHubIdentity) -> str:
        """The resolved email, unless a different account already owns it.

        Without this guard a GitHub user whose address matches the existing
        email-identified account would trip the ``unique`` constraint with a 500.
        The synthesised noreply address is derived from the unique ``github_id``, so
        it can never collide.
        """
        if User.objects.filter(email=identity.email.lower()).exists():
            return identity.noreply_email
        return identity.email


class GitHubConfigView(APIView):
    """Public device-flow parameters, so the CLI never hardcodes the OAuth app id."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(responses={200: GitHubConfigSerializer})
    def get(self, request: Request) -> Response:
        return Response(
            {
                "client_id": django_settings.GITHUB_CLIENT_ID,
                "scopes": list(django_settings.GITHUB_OAUTH_SCOPES),
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
