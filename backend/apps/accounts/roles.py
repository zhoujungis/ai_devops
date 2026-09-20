"""Role vocabulary shared by organization and project memberships."""

from __future__ import annotations

from django.db import models

# Ordering is the single source of truth for "at least this role" checks.
_RANK: dict[str, int] = {
    "viewer": 1,
    "qa": 2,
    "developer": 3,
    "pm": 4,
    "admin": 5,
}


class Role(models.TextChoices):
    VIEWER = "viewer", "Viewer"
    QA = "qa", "QA"
    DEVELOPER = "developer", "Developer"
    PM = "pm", "PM"
    ADMIN = "admin", "Admin"

    @property
    def rank(self) -> int:
        return _RANK[self.value]

    def at_least(self, minimum: Role) -> bool:
        return self.rank >= minimum.rank

    @classmethod
    def from_value(cls, value: str) -> Role:
        return cls(value)


def coerce(value: str | Role | None) -> Role | None:
    """Normalise a stored value (plain string) into a :class:`Role`."""
    if value is None:
        return None
    return value if isinstance(value, Role) else Role(value)


def highest(*values: str | Role | None) -> Role | None:
    """Return the most privileged of the given roles, ignoring absent ones.

    Organization membership sets the baseline for every project; a project
    membership can elevate it but never reduce it.
    """
    candidates = [role for role in (coerce(value) for value in values) if role is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda role: role.rank)
