"""Release serializers."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.core.serializers import ProjectScopedUniqueMixin
from apps.releases.models import Release, ReleaseCommitLink, ReleaseRiskSnapshot


class ReleaseRiskSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReleaseRiskSnapshot
        fields = ("id", "score", "level", "breakdown", "computed_at")
        read_only_fields = fields


class ReleaseCommitLinkSerializer(serializers.ModelSerializer):
    commit = serializers.SerializerMethodField()

    class Meta:
        model = ReleaseCommitLink
        fields = ("commit", "source", "confidence")

    def get_commit(self, obj: ReleaseCommitLink) -> dict[str, str]:
        return {
            "id": str(obj.commit_id),
            "sha": obj.commit.sha,
            "short_sha": obj.commit.short_sha,
            "message": obj.commit.message.splitlines()[0] if obj.commit.message else "",
        }


class ReleaseSerializer(ProjectScopedUniqueMixin, serializers.ModelSerializer):
    commit_links = ReleaseCommitLinkSerializer(many=True, read_only=True)
    latest_risk = serializers.SerializerMethodField()

    unique_fields = ("version",)

    class Meta:
        model = Release
        fields = (
            "id",
            "project",
            "version",
            "name",
            "status",
            "planned_at",
            "released_at",
            "notes",
            "commit_links",
            "latest_risk",
            "created_at",
        )
        read_only_fields = ("id", "project", "commit_links", "latest_risk", "created_at")

    def get_latest_risk(self, obj: Release) -> dict[str, Any] | None:
        """The most recent risk snapshot, so the UI does not have to hunt for it."""
        snapshot = obj.risk_snapshots.first()
        if snapshot is None:
            return None
        data: dict[str, Any] = ReleaseRiskSnapshotSerializer(snapshot).data
        return data
