"""Serialisers for risk assessments.

These serialise the engine's dataclass rather than an ORM row, because the score is
computed on demand from current data. A stored snapshot would be stale the moment
anything changed.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.risk.engine import RiskAssessment


class RiskAssessmentSerializer(serializers.Serializer):
    """The score plus its full decomposition.

    ``breakdown`` is emitted as plain objects rather than through a nested
    serializer: one of its keys is ``label``, which collides with DRF's own
    ``Field.label`` attribute. The shape is identical either way.
    """

    score = serializers.FloatField()
    level = serializers.CharField()
    breakdown = serializers.SerializerMethodField()

    def get_breakdown(self, obj: RiskAssessment) -> list[dict[str, Any]]:
        return obj.as_breakdown()
