from __future__ import annotations

import pytest

from apps.accounts.roles import Role, coerce, highest


def test_roles_are_ordered_from_least_to_most_privileged() -> None:
    assert Role.VIEWER.rank < Role.QA.rank < Role.DEVELOPER.rank < Role.PM.rank < Role.ADMIN.rank


@pytest.mark.parametrize(
    ("role", "minimum", "expected"),
    [
        (Role.ADMIN, Role.ADMIN, True),
        (Role.ADMIN, Role.VIEWER, True),
        (Role.VIEWER, Role.ADMIN, False),
        (Role.QA, Role.DEVELOPER, False),
        (Role.DEVELOPER, Role.DEVELOPER, True),
        (Role.PM, Role.ADMIN, False),
    ],
)
def test_at_least(role: Role, minimum: Role, expected: bool) -> None:
    assert role.at_least(minimum) is expected


def test_highest_picks_the_most_privileged_role() -> None:
    assert highest("viewer", Role.PM, Role.DEVELOPER) is Role.PM


def test_highest_ignores_absent_roles() -> None:
    assert highest(None, Role.QA, None) is Role.QA


def test_highest_returns_none_when_nothing_is_held() -> None:
    assert highest(None, None) is None


def test_coerce_normalises_stored_strings() -> None:
    assert coerce("developer") is Role.DEVELOPER
    assert coerce(None) is None
