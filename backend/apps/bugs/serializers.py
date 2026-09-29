"""Bug serializers."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from rest_framework import serializers

from apps.bugs.models import (
    Bug,
    BugModuleLink,
    BugOccurrence,
    BugRelation,
    BugTestCaseLink,
)
from apps.core.serializers import ProjectScopedUniqueMixin
from apps.testing.models import TestCase


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


class BugRelationSerializer(serializers.ModelSerializer):
    """A declared edge to another bug: a duplicate, a similar, a cause.

    ``source`` stays read-only and so defaults to manual — a relation a person typed is
    exactly the kind that must be distinguishable from anything inferred.
    """

    class Meta:
        model = BugRelation
        fields = ("id", "related_bug", "kind", "confidence", "source")
        read_only_fields = ("id", "source")


class BugSerializer(ProjectScopedUniqueMixin, serializers.ModelSerializer):
    """A bug is written together with its occurrences.

    ``occurrences`` replaces the set, and the denormalised first/last-seen timestamps
    and count are recomputed from it — so the trajectory the correlation chain reads
    can never disagree with the sightings it was derived from.
    """

    module_links = BugModuleLinkSerializer(many=True, read_only=True)
    occurrences = BugOccurrenceSerializer(many=True, required=False)
    relations = BugRelationSerializer(many=True, required=False)
    #: Which cases reproduce or guard this bug. Ids rather than nested rows, because the
    #: write shape is a list of ids while the read shape (through the case) is not.
    test_case_ids = serializers.ListField(
        child=serializers.UUIDField(), write_only=True, required=False
    )

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
            "relations",
            "test_case_ids",
            "created_at",
        )
        read_only_fields = (
            "id",
            "project",
            "first_seen_at",
            "last_seen_at",
            "occurrence_count",
            "module_links",
            "created_at",
        )

    def validate_key(self, value: str) -> str:
        return value.strip().upper()

    def validate_occurrences(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        project = self.context.get("project")
        if project is None:
            return value
        for row in value:
            release = row.get("release")
            if release is not None and release.project_id != project.pk:
                raise serializers.ValidationError(
                    "An occurrence may only reference a release in this project."
                )
            test_run = row.get("test_run")
            if test_run is not None and test_run.project_id != project.pk:
                raise serializers.ValidationError(
                    "An occurrence may only reference a test run in this project."
                )
        return value

    def validate_relations(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        project = self.context.get("project")
        if project is None:
            return value
        for row in value:
            related = row.get("related_bug")
            if related is not None and related.project_id != project.pk:
                raise serializers.ValidationError("A related bug must be in this project.")
        return value

    def validate_test_case_ids(self, value: list[Any]) -> list[Any]:
        project = self.context.get("project")
        if project is None or not value:
            return value
        found = set(
            TestCase.objects.filter(pk__in=value, project=project).values_list("pk", flat=True)
        )
        missing = [str(pk) for pk in value if pk not in found]
        if missing:
            raise serializers.ValidationError(
                f"These test cases are not in this project: {', '.join(missing)}"
            )
        return value

    @transaction.atomic
    def create(self, validated_data: dict[str, Any]) -> Bug:
        occurrences = validated_data.pop("occurrences", [])
        relations = validated_data.pop("relations", None)
        test_case_ids = validated_data.pop("test_case_ids", None)
        bug = super().create(validated_data)
        self._replace_occurrences(bug, occurrences)
        if relations is not None:
            self._replace_relations(bug, relations)
        if test_case_ids is not None:
            self._replace_test_cases(bug, test_case_ids)
        return bug

    @transaction.atomic
    def update(self, instance: Bug, validated_data: dict[str, Any]) -> Bug:
        occurrences = validated_data.pop("occurrences", None)
        relations = validated_data.pop("relations", None)
        test_case_ids = validated_data.pop("test_case_ids", None)
        bug = super().update(instance, validated_data)
        if occurrences is not None:
            self._replace_occurrences(bug, occurrences)
        if relations is not None:
            self._replace_relations(bug, relations)
        if test_case_ids is not None:
            self._replace_test_cases(bug, test_case_ids)
        return bug

    @staticmethod
    def _replace_occurrences(bug: Bug, occurrences: list[dict[str, Any]]) -> None:
        bug.occurrences.all().delete()
        BugOccurrence.objects.bulk_create(
            [BugOccurrence(bug=bug, **row) for row in occurrences]
        )
        rows = list(bug.occurrences.values_list("seen_at", "count"))
        if not rows:
            return
        bug.first_seen_at = min(seen_at for seen_at, _ in rows)
        bug.last_seen_at = max(seen_at for seen_at, _ in rows)
        bug.occurrence_count = sum(count for _, count in rows)
        bug.save(
            update_fields=["first_seen_at", "last_seen_at", "occurrence_count", "updated_at"]
        )

    @staticmethod
    def _replace_relations(bug: Bug, relations: list[dict[str, Any]]) -> None:
        bug.relations.all().delete()
        BugRelation.objects.bulk_create(
            [
                BugRelation(
                    bug=bug,
                    related_bug=row["related_bug"],
                    kind=row["kind"],
                    confidence=row.get("confidence") or 1.0,
                )
                for row in relations
            ]
        )

    @staticmethod
    def _replace_test_cases(bug: Bug, test_case_ids: list[Any]) -> None:
        bug.test_case_links.all().delete()
        BugTestCaseLink.objects.bulk_create(
            [BugTestCaseLink(bug=bug, test_case_id=test_case_id) for test_case_id in test_case_ids]
        )
