"""Role-based access control.

Two layers work together and produce deliberately different failures:

* **Visibility** — :func:`apps.core.scoping.scoped_queryset` narrows every
  lookup, so a resource owned by another tenant is simply absent. That yields
  ``404`` and never confirms that the other tenant exists.
* **Authority** — :class:`RoleRequired` rejects an action when the caller's role
  in the addressed scope is too low. That yields ``403``: the caller can see the
  resource, they just may not do this to it.
"""

from __future__ import annotations

from typing import Any, ClassVar, TypeAlias, cast

from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework.permissions import BasePermission

from apps.accounts.models import Organization, Project, User
from apps.accounts.roles import Role

#: Either a minimum role (hierarchical) or the exact set allowed.
RoleRequirement: TypeAlias = Role | frozenset[Role]


def _satisfies(role: Role, requirement: RoleRequirement) -> bool:
    if isinstance(requirement, frozenset):
        return role in requirement
    return role.at_least(requirement)


class ScopedRoleViewMixin:
    """Resolves the tenant scope addressed by the URL.

    Every scoped endpoint carries its organization (and project, when nested) in
    the URL, so the scope is always resolvable without trusting the request body.
    Both resolvers go through the caller's scoped queryset, which is what turns
    "another tenant's organization" into a 404.
    """

    # Provided by the DRF view this mixin is combined with.
    request: Any
    kwargs: dict[str, Any]
    action: str

    #: Action name -> minimum role, or an explicit set when the rule is not a
    #: hierarchy. "QA may edit test cases but PM may not" cannot be expressed as a
    #: minimum, so both forms are supported.
    required_roles: ClassVar[dict[str, RoleRequirement]] = {}

    def current_user(self) -> User:
        """The caller. ``IsAuthenticated`` guarantees this is a real user."""
        return cast(User, self.request.user)

    def get_scope_org(self) -> Organization | None:
        org_pk = self.kwargs.get("org_pk")
        if org_pk is None:
            return None
        org: Organization = get_object_or_404(
            Organization.objects.accessible_to(self.request.user), pk=org_pk
        )
        return org

    def get_scope_project(self) -> Project | None:
        project_pk = self.kwargs.get("project_pk")
        if project_pk is None:
            return None
        project: Project = get_object_or_404(
            Project.objects.accessible_to(self.request.user),
            pk=project_pk,
            org=self.get_scope_org(),
        )
        return project

    def required_role_for_action(self) -> RoleRequirement | None:
        return self.required_roles.get(self.action)

    def get_scope_role(self) -> Role | None:
        """The caller's effective role in the addressed scope."""
        project = self.get_scope_project()
        if project is not None:
            return project.role_for(self.request.user)
        org = self.get_scope_org()
        if org is not None:
            return org.role_for(self.request.user)
        return None


class RoleRequired(BasePermission):
    """Enforces :attr:`ScopedRoleViewMixin.required_roles` for the current action."""

    message = "Your role in this scope does not allow this action."

    def has_permission(self, request: Any, view: Any) -> bool:
        required_role_for_action = getattr(view, "required_role_for_action", None)
        if required_role_for_action is None:
            return True  # View does not participate in role gating.
        required: RoleRequirement | None = required_role_for_action()
        if required is None:
            return True

        scope_role: Role | None = view.get_scope_role()
        if scope_role is None:
            # No tenant in the URL (for example "create an organization"). Only the
            # baseline is grantable here; anything wider must be scoped, so this
            # fails closed rather than assuming authority.
            return _satisfies(Role.VIEWER, required)

        # Returning False produces a 403 on purpose: the caller is a member, so the
        # resource is visible to them and a 404 would be a lie.
        return _satisfies(scope_role, required)


def require_scope_org(view: Any) -> Organization:
    """Return the view's organization, or raise 404 when the caller cannot see it."""
    org = view.get_scope_org()
    if org is None:
        raise Http404
    return org


def require_scope_project(view: Any) -> Project:
    """Return the view's project, or raise 404 when the caller cannot see it."""
    project = view.get_scope_project()
    if project is None:
        raise Http404
    return project
