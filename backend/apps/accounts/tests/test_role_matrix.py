"""The role matrix, asserted end to end.

For every (role, action) pair this checks the real HTTP outcome, so a change to
``required_roles`` that silently widens access fails here. Two invariants:

* a role at or above the action's minimum succeeds,
* a role below it gets **403** (never 404) — the caller is a member, so the
  resource is visible to them; they simply may not perform the action.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.accounts.roles import Role
from apps.accounts.tests.factories import Scene, build_scene

pytestmark = pytest.mark.django_db


@dataclass(frozen=True)
class Case:
    name: str
    method: str
    target: str
    minimum: Role
    payload: Callable[[Scene], dict[str, Any]] | None = None


CASES: list[Case] = [
    Case("org_retrieve", "get", "org", Role.VIEWER),
    Case("org_update", "patch", "org", Role.ADMIN, lambda scene: {"name": "Renamed"}),
    Case("org_delete", "delete", "org", Role.ADMIN),
    Case("org_member_list", "get", "org_members", Role.VIEWER),
    Case(
        "org_member_create",
        "post",
        "org_members",
        Role.ADMIN,
        lambda scene: {"user_email": scene.outsider.email, "role": Role.VIEWER},
    ),
    Case("org_member_update", "patch", "org_member", Role.ADMIN, lambda scene: {"role": Role.QA}),
    Case("org_member_delete", "delete", "org_member", Role.ADMIN),
    Case("project_list", "get", "projects", Role.VIEWER),
    Case("project_create", "post", "projects", Role.PM, lambda scene: {"name": "Checkout"}),
    Case("project_retrieve", "get", "project", Role.VIEWER),
    Case(
        "project_update",
        "patch",
        "project",
        Role.PM,
        lambda scene: {"description": "touched"},
    ),
    Case("project_delete", "delete", "project", Role.ADMIN),
    Case("project_member_list", "get", "project_members", Role.VIEWER),
    Case(
        "project_member_create",
        "post",
        "project_members",
        Role.PM,
        lambda scene: {"user_email": scene.outsider.email, "role": Role.VIEWER},
    ),
    Case("project_member_delete", "delete", "project_member", Role.PM),
]


@pytest.mark.parametrize("role", list(Role), ids=[role.value for role in Role])
@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_role_matrix(case: Case, role: Role) -> None:
    scene = build_scene(role)
    client = APIClient()
    client.force_authenticate(user=scene.actor)

    payload = case.payload(scene) if case.payload is not None else {}
    response = getattr(client, case.method)(scene.urls()[case.target], payload, format="json")

    if role.at_least(case.minimum):
        assert response.status_code < 400, (
            f"{role.value} should be allowed to {case.method} {case.name}, "
            f"got {response.status_code}: {response.content[:300]!r}"
        )
    else:
        assert response.status_code == 403, (
            f"{role.value} must be forbidden from {case.method} {case.name}, "
            f"got {response.status_code}: {response.content[:300]!r}"
        )
