"""Domain rules and operations that guard tenancy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Membership
from apps.accounts.roles import Role
from apps.core.exceptions import ApplicationError

if TYPE_CHECKING:
    from apps.accounts.models import User


class LastAdminError(ApplicationError):
    """Raised when a change would leave an organization without any admin."""

    status_code: int = status.HTTP_409_CONFLICT
    default_detail: str = "An organization must keep at least one admin."
    default_code: str = "last_admin"


def assert_admin_survives(membership: Membership) -> None:
    """Refuse a removal or demotion that would orphan the organization.

    Every other guard is a role check; this one is an invariant, which is why it
    lives in a service rather than in a permission class.

    Must be called inside a transaction. The check counts the *other* admins and the
    caller then writes, which is a read-then-write race: two concurrent demotions could
    each see a surviving admin and both proceed, leaving the organization with none.
    ``select_for_update`` locks the organization's admin rows so the second caller waits
    and then observes the first one's committed change.
    """
    if membership.role != Role.ADMIN:
        return
    others = list(
        Membership.objects.select_for_update()
        .filter(org_id=membership.org_id, role=Role.ADMIN)
        .exclude(pk=membership.pk)
        .values_list("pk", flat=True)
    )
    if not others:
        raise LastAdminError()


def issue_tokens(user: User) -> dict[str, str]:
    """Mint an access/refresh pair for ``user``."""
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}
