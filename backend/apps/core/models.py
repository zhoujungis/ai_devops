"""Abstract base models and reusable field types."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from django.db import models

from apps.core.crypto import decrypt, encrypt


class UUIDModel(models.Model):
    """Primary key as a UUID so identifiers are safe to expose in URLs and logs."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BaseModel(UUIDModel, TimeStampedModel):
    """Default base for domain models: UUID pk + created/updated timestamps."""

    class Meta:
        abstract = True
        # Annotated so a subclass may order by more than one field: without it mypy
        # infers the precise one-element tuple type from the default below and then
        # rejects every two-field ordering.
        ordering: ClassVar[tuple[str, ...]] = ("-created_at",)


class EncryptedTextField(models.TextField):
    """TextField whose value is encrypted at rest.

    Encryption is non-deterministic (a fresh IV per write), so columns using this
    field cannot be used in ``filter()``/``exclude()`` lookups.
    """

    description = "Text encrypted at rest with Fernet"

    def get_prep_value(self, value: Any) -> Any:
        if value is None or value == "":
            return value
        return encrypt(str(value))

    def from_db_value(self, value: Any, expression: Any, connection: Any) -> Any:
        if value is None or value == "":
            return value
        return decrypt(str(value))

    def to_python(self, value: Any) -> Any:
        return value


class LinkSource(models.TextChoices):
    MANUAL = "manual", "Manual"
    INFERRED = "inferred", "Inferred from data"
    AI = "ai", "AI suggested"


class EntityLink(BaseModel):
    """Abstract base for an edge between two entities.

    Used instead of a bare ``ManyToManyField`` everywhere the relationship is not
    strictly one-to-one, so that every edge records *how* it was established and how
    much to trust it. An AI-inferred edge has to be distinguishable from one a human
    declared — and revocable — which a plain many-to-many cannot express.
    """

    source = models.CharField(max_length=16, choices=LinkSource.choices, default=LinkSource.MANUAL)
    #: 1.0 for a human declaration; lower for anything we inferred or guessed.
    confidence = models.FloatField(default=1.0)

    class Meta:
        abstract = True
        ordering = ("-created_at",)
