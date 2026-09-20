"""Bugs, their occurrences and the evidence that links them to code."""

from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings
from django.db import models
from pgvector.django import VectorField

from apps.accounts.models import Project
from apps.core.enums import Priority
from apps.core.models import BaseModel, EntityLink, LinkSource
from apps.core.scoping import ScopedModel


class Severity(models.TextChoices):
    S1 = "s1", "S1 - Blocker"
    S2 = "s2", "S2 - Critical"
    S3 = "s3", "S3 - Major"
    S4 = "s4", "S4 - Minor"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self.value]

    def at_least(self, minimum: Severity) -> bool:
        """True when this severity is as bad as, or worse than, ``minimum``."""
        return self.rank <= minimum.rank


_SEVERITY_RANK: dict[str, int] = {"s1": 0, "s2": 1, "s3": 2, "s4": 3}


class BugStatus(models.TextChoices):
    OPEN = "open", "Open"
    IN_PROGRESS = "in_progress", "In progress"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"
    REOPENED = "reopened", "Reopened"


class Bug(BaseModel, ScopedModel):
    """A defect, addressed by the human key BUG-1023."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="bugs")
    key = models.CharField(max_length=64)
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True, default="")
    severity = models.CharField(max_length=4, choices=Severity.choices, default=Severity.S3)
    priority = models.CharField(max_length=4, choices=Priority.choices, default=Priority.P2)
    status = models.CharField(max_length=16, choices=BugStatus.choices, default=BugStatus.OPEN)
    error_type = models.CharField(max_length=200, blank=True, default="")
    stack_trace = models.TextField(blank=True, default="")
    environment = models.CharField(max_length=100, blank=True, default="")

    #: Denormalised from BugOccurrence so the correlation query stays a single join.
    first_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    occurrence_count = models.PositiveIntegerField(default=0)

    reporter = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reported_bugs",
    )
    assignee = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_bugs",
    )
    #: Written when embeddings are computed (S7); powers "have we seen this before".
    summary_embedding = VectorField(dimensions=django_settings.EMBEDDING_DIM, null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(fields=["project", "key"], name="uniq_bug_project_key"),
        ]

    def __str__(self) -> str:
        return f"{self.key} {self.title}"

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[Bug]:
        return cls.objects.filter(project__in=Project.objects.accessible_to(user))


class BugOccurrence(BaseModel):
    """One sighting of a bug, so a spike can be correlated with a release."""

    bug = models.ForeignKey(Bug, on_delete=models.CASCADE, related_name="occurrences")
    seen_at = models.DateTimeField(db_index=True)
    environment = models.CharField(max_length=100, blank=True, default="")
    count = models.PositiveIntegerField(default=1)
    release = models.ForeignKey(
        "releases.Release",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bug_occurrences",
    )
    test_run = models.ForeignKey(
        "testing.TestRun",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bug_occurrences",
    )

    class Meta(BaseModel.Meta):
        ordering = ("-seen_at",)

    def __str__(self) -> str:
        return f"{self.bug.key} @ {self.seen_at:%Y-%m-%d}"


class BugRelationKind(models.TextChoices):
    SIMILAR = "similar", "Similar"
    DUPLICATE = "duplicate", "Duplicate"
    CAUSED_BY = "caused_by", "Caused by"
    BLOCKS = "blocks", "Blocks"


class BugRelation(BaseModel):
    """A directed edge between two bugs, so "similar to" is queryable."""

    bug = models.ForeignKey(Bug, on_delete=models.CASCADE, related_name="relations")
    related_bug = models.ForeignKey(Bug, on_delete=models.CASCADE, related_name="related_from")
    kind = models.CharField(max_length=16, choices=BugRelationKind.choices)
    confidence = models.FloatField(default=1.0)
    source = models.CharField(max_length=16, choices=LinkSource.choices, default=LinkSource.MANUAL)

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["bug", "related_bug", "kind"], name="uniq_bug_relation"
            ),
            models.CheckConstraint(
                condition=~models.Q(bug=models.F("related_bug")),
                name="bug_relation_not_self",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.bug.key} {self.kind} {self.related_bug.key}"


class BugModuleLink(EntityLink):
    """Which module a bug lives in. A bug can implicate several."""

    bug = models.ForeignKey(Bug, on_delete=models.CASCADE, related_name="module_links")
    module = models.ForeignKey(
        "codebase.Module", on_delete=models.CASCADE, related_name="bug_links"
    )

    class Meta(EntityLink.Meta):
        constraints = [
            models.UniqueConstraint(fields=["bug", "module"], name="uniq_bug_module_link"),
        ]

    def __str__(self) -> str:
        return f"{self.bug.key} -> {self.module.name}"


class BugCommitLink(EntityLink):
    """A commit associated with a bug: the fix, the cause, or the introduction."""

    bug = models.ForeignKey(Bug, on_delete=models.CASCADE, related_name="commit_links")
    commit = models.ForeignKey(
        "codebase.Commit", on_delete=models.CASCADE, related_name="bug_links"
    )

    class Meta(EntityLink.Meta):
        constraints = [
            models.UniqueConstraint(fields=["bug", "commit"], name="uniq_bug_commit_link"),
        ]

    def __str__(self) -> str:
        return f"{self.bug.key} @ {self.commit.short_sha}"


class BugTestCaseLink(EntityLink):
    """A test case that either reproduces or guards this bug."""

    bug = models.ForeignKey(Bug, on_delete=models.CASCADE, related_name="test_case_links")
    test_case = models.ForeignKey(
        "testing.TestCase", on_delete=models.CASCADE, related_name="bug_links"
    )

    class Meta(EntityLink.Meta):
        constraints = [
            models.UniqueConstraint(fields=["bug", "test_case"], name="uniq_bug_testcase_link"),
        ]

    def __str__(self) -> str:
        return f"{self.bug.key} -> {self.test_case.key}"


class BugRequirementLink(EntityLink):
    bug = models.ForeignKey(Bug, on_delete=models.CASCADE, related_name="requirement_links")
    requirement = models.ForeignKey(
        "requirements.Requirement", on_delete=models.CASCADE, related_name="bug_links"
    )

    class Meta(EntityLink.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["bug", "requirement"], name="uniq_bug_requirement_link"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.bug.key} -> {self.requirement.external_key}"
