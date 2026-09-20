"""Shared vocabularies.

Values that more than one app needs live here so the meaning of "P1" cannot drift
between requirements, test cases and bugs.
"""

from __future__ import annotations

from django.db import models


class Priority(models.TextChoices):
    P0 = "p0", "P0 - Critical"
    P1 = "p1", "P1 - High"
    P2 = "p2", "P2 - Medium"
    P3 = "p3", "P3 - Low"

    @property
    def rank(self) -> int:
        return _PRIORITY_RANK[self.value]

    def at_least(self, minimum: Priority) -> bool:
        """True when this priority is as urgent as, or more urgent than, ``minimum``."""
        return self.rank <= minimum.rank


_PRIORITY_RANK: dict[str, int] = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}
