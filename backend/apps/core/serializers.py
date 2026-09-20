"""Serializer building blocks shared across apps."""

from __future__ import annotations

from typing import Any, ClassVar

from rest_framework import serializers


class ProjectScopedUniqueMixin:
    """Validate a unique-per-project field before the database has to.

    The owning project comes from the URL, never the request body, so DRF cannot
    build its automatic unique-together validator: that validator needs the parent
    value to be present in the input. Without this the constraint is only enforced
    by the database, and a duplicate arrives as a 500 instead of a field error.
    """

    #: Fields that must be unique within the project, e.g. ``("external_key",)``.
    unique_fields: ClassVar[tuple[str, ...]] = ()

    # Provided by the concrete serializer this mixin is combined with.
    instance: Any
    context: dict[str, Any]
    Meta: ClassVar[Any]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = super().validate(attrs)  # type: ignore[misc]

        project = self.context.get("project")
        model = getattr(self.Meta, "model", None)
        if project is None or model is None:
            return attrs

        for field in self.unique_fields:
            value = attrs.get(field, getattr(self.instance, field, None))
            if value in (None, ""):
                continue

            clashes = model.objects.filter(project=project, **{field: value})
            if self.instance is not None:
                clashes = clashes.exclude(pk=self.instance.pk)
            if clashes.exists():
                label = field.replace("_", " ")
                raise serializers.ValidationError(
                    {field: f"A record with this {label} already exists in the project."}
                )
        return attrs
