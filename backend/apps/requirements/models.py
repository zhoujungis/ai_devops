"""Requirements: the top of the chain the whole product exists to connect."""

from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings
from django.db import models

from apps.accounts.models import Project
from apps.core.enums import Priority
from apps.core.models import BaseModel, EntityLink
from apps.core.scoping import ScopedModel


class RequirementSource(models.TextChoices):
    MANUAL = "manual", "Entered by hand"
    JIRA = "jira", "Issue tracker"
    DOCUMENT = "document", "Document"
    IMPORTED = "imported", "Imported"


class RequirementStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    APPROVED = "approved", "Approved"
    IN_PROGRESS = "in_progress", "In progress"
    IMPLEMENTED = "implemented", "Implemented"
    VERIFIED = "verified", "Verified"
    RELEASED = "released", "Released"
    CANCELLED = "cancelled", "Cancelled"


class Requirement(BaseModel, ScopedModel):
    """One requirement, addressed by the human key used in commits and branches."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="requirements")
    #: The key people actually type (PAY-18). Commit messages are matched against it.
    external_key = models.CharField(max_length=64)
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True, default="")
    source = models.CharField(
        max_length=16, choices=RequirementSource.choices, default=RequirementSource.MANUAL
    )
    source_url = models.URLField(blank=True, default="")
    status = models.CharField(
        max_length=16, choices=RequirementStatus.choices, default=RequirementStatus.DRAFT
    )
    priority = models.CharField(max_length=4, choices=Priority.choices, default=Priority.P2)
    sprint = models.CharField(max_length=100, blank=True, default="")
    acceptance_criteria = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="requirements",
    )

    class Meta(BaseModel.Meta):
        ordering = ("external_key",)
        constraints = [
            models.UniqueConstraint(
                fields=["project", "external_key"], name="uniq_requirement_project_key"
            ),
        ]
        # The requirement list filters on status and priority within a project.
        indexes = [
            models.Index(fields=["project", "status"], name="idx_requirement_project_status"),
        ]

    def __str__(self) -> str:
        return f"{self.external_key} {self.title}"

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[Requirement]:
        return cls.objects.filter(project__in=Project.objects.accessible_to(user))


class RequirementItemType(models.TextChoices):
    SCENARIO = "scenario", "Scenario"
    CONSTRAINT = "constraint", "Constraint"
    EDGE_CASE = "edge_case", "Edge case"
    ACCEPTANCE = "acceptance", "Acceptance criterion"


class RequirementItem(BaseModel):
    """One extracted clause of a requirement: a scenario, constraint or criterion."""

    requirement = models.ForeignKey(Requirement, on_delete=models.CASCADE, related_name="items")
    seq = models.PositiveSmallIntegerField(default=1)
    type = models.CharField(
        max_length=16, choices=RequirementItemType.choices, default=RequirementItemType.SCENARIO
    )
    text = models.TextField()

    class Meta(BaseModel.Meta):
        ordering = ("requirement", "seq")
        constraints = [
            models.UniqueConstraint(
                fields=["requirement", "seq"], name="uniq_requirement_item_seq"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.requirement.external_key}#{self.seq}"


class ModuleRequirementLink(EntityLink):
    """Which module implements a requirement. Many-to-many, with provenance."""

    requirement = models.ForeignKey(
        Requirement, on_delete=models.CASCADE, related_name="module_links"
    )
    module = models.ForeignKey(
        "codebase.Module", on_delete=models.CASCADE, related_name="requirement_links"
    )

    class Meta(EntityLink.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["requirement", "module"], name="uniq_module_requirement_link"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.requirement.external_key} -> {self.module.name}"
