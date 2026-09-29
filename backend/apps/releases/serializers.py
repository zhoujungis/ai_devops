"""Release serializers."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from rest_framework import serializers

from apps.codebase.models import Commit
from apps.core.serializers import ProjectScopedUniqueMixin
from apps.releases.models import Release, ReleaseCommitLink


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
    """A release is written together with the commits it shipped.

    ``commit_ids`` replaces the set. It is a separate write-only field rather than a
    writable ``commit_links`` because the read shape is a nested object (sha, message)
    and the write shape is a list of ids; overloading one field with two shapes is how
    clients end up sending the wrong one.
    """

    commit_links = ReleaseCommitLinkSerializer(many=True, read_only=True)
    commit_ids = serializers.ListField(
        child=serializers.UUIDField(), write_only=True, required=False
    )

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
            "commit_ids",
            "created_at",
        )
        read_only_fields = ("id", "project", "commit_links", "created_at")

    def validate_commit_ids(self, value: list[Any]) -> list[Any]:
        project = self.context.get("project")
        if project is None or not value:
            return value
        found = set(
            Commit.objects.filter(
                pk__in=value, repository__project=project
            ).values_list("pk", flat=True)
        )
        missing = [str(pk) for pk in value if pk not in found]
        if missing:
            raise serializers.ValidationError(
                f"These commits are not in this project: {', '.join(missing)}"
            )
        return value

    @transaction.atomic
    def create(self, validated_data: dict[str, Any]) -> Release:
        commit_ids = validated_data.pop("commit_ids", None)
        release = super().create(validated_data)
        if commit_ids is not None:
            self._replace_commits(release, commit_ids)
        return release

    @transaction.atomic
    def update(self, instance: Release, validated_data: dict[str, Any]) -> Release:
        commit_ids = validated_data.pop("commit_ids", None)
        release = super().update(instance, validated_data)
        if commit_ids is not None:
            self._replace_commits(release, commit_ids)
        return release

    @staticmethod
    def _replace_commits(release: Release, commit_ids: list[Any]) -> None:
        release.commit_links.all().delete()
        ReleaseCommitLink.objects.bulk_create(
            [ReleaseCommitLink(release=release, commit_id=commit_id) for commit_id in commit_ids]
        )
