"""Bug serializers."""

from __future__ import annotations

from rest_framework import serializers

from apps.bugs.models import Bug, BugModuleLink, BugOccurrence
from apps.core.serializers import ProjectScopedUniqueMixin


class BugModuleLinkSerializer(serializers.ModelSerializer):
    module = serializers.SerializerMethodField()

    class Meta:
        model = BugModuleLink
        fields = ("module", "source", "confidence")

    def get_module(self, obj: BugModuleLink) -> dict[str, str]:
        return {
            "id": str(obj.module_id),
            "name": obj.module.name,
            "path_prefix": obj.module.path_prefix,
        }


class BugOccurrenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = BugOccurrence
        fields = ("id", "seen_at", "environment", "count", "release", "test_run")
        read_only_fields = ("id",)


class BugSerializer(ProjectScopedUniqueMixin, serializers.ModelSerializer):
    module_links = BugModuleLinkSerializer(many=True, read_only=True)
    occurrences = BugOccurrenceSerializer(many=True, read_only=True)

    unique_fields = ("key",)

    class Meta:
        model = Bug
        fields = (
            "id",
            "project",
            "key",
            "title",
            "description",
            "severity",
            "priority",
            "status",
            "error_type",
            "stack_trace",
            "environment",
            "first_seen_at",
            "last_seen_at",
            "occurrence_count",
            "reporter",
            "assignee",
            "module_links",
            "occurrences",
            "created_at",
        )
        read_only_fields = (
            "id",
            "project",
            "first_seen_at",
            "last_seen_at",
            "occurrence_count",
            "module_links",
            "occurrences",
            "created_at",
        )

    def validate_key(self, value: str) -> str:
        return value.strip().upper()
