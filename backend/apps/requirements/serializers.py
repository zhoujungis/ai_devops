"""Requirement serializers."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from rest_framework import serializers

from apps.core.serializers import ProjectScopedUniqueMixin
from apps.requirements.models import ModuleRequirementLink, Requirement, RequirementItem


class RequirementItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = RequirementItem
        fields = ("id", "seq", "type", "text")
        read_only_fields = ("id",)


class RequirementModuleLinkSerializer(serializers.ModelSerializer):
    module = serializers.SerializerMethodField()

    class Meta:
        model = ModuleRequirementLink
        fields = ("module", "source", "confidence")

    def get_module(self, obj: ModuleRequirementLink) -> dict[str, Any]:
        return {
            "id": str(obj.module_id),
            "name": obj.module.name,
            "path_prefix": obj.module.path_prefix,
        }


class RequirementSerializer(ProjectScopedUniqueMixin, serializers.ModelSerializer):
    """Requirements are written with their extracted items in one request.

    ``items`` replaces the set rather than merging it: the list is a document, and
    partial merges make "did my edit stick" unanswerable.
    """

    items = RequirementItemSerializer(many=True, required=False)
    module_links = RequirementModuleLinkSerializer(many=True, read_only=True)

    unique_fields = ("external_key",)

    class Meta:
        model = Requirement
        fields = (
            "id",
            "project",
            "external_key",
            "title",
            "description",
            "source",
            "source_url",
            "status",
            "priority",
            "sprint",
            "acceptance_criteria",
            "items",
            "module_links",
            "created_at",
        )
        read_only_fields = ("id", "project", "module_links", "created_at")

    def validate_external_key(self, value: str) -> str:
        return value.strip().upper()

    @transaction.atomic
    def create(self, validated_data: dict[str, Any]) -> Requirement:
        items = validated_data.pop("items", [])
        requirement = super().create(validated_data)
        self._replace_items(requirement, items)
        return requirement

    @transaction.atomic
    def update(self, instance: Requirement, validated_data: dict[str, Any]) -> Requirement:
        items = validated_data.pop("items", None)
        requirement = super().update(instance, validated_data)
        if items is not None:
            self._replace_items(requirement, items)
        return requirement

    @staticmethod
    def _replace_items(requirement: Requirement, items: list[dict[str, Any]]) -> None:
        # Delete-then-insert must be atomic: a failed bulk_create would otherwise
        # leave the requirement with no items at all.
        requirement.items.all().delete()
        RequirementItem.objects.bulk_create(
            [
                RequirementItem(
                    requirement=requirement,
                    seq=item.get("seq", index + 1),
                    type=item.get("type", "scenario"),
                    text=item["text"],
                )
                for index, item in enumerate(items)
            ]
        )
