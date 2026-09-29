"""Releases and the commit ranges they shipped."""

from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings
from django.db import models

from apps.accounts.models import Project
from apps.core.models import BaseModel, EntityLink
from apps.core.scoping import ScopedModel


class ReleaseStatus(models.TextChoices):
    PLANNED = "planned", "Planned"
    RELEASED = "released", "Released"
    ROLLED_BACK = "rolled_back", "Rolled back"


class Release(BaseModel, ScopedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="releases")
    version = models.CharField(max_length=100)
    name = models.CharField(max_length=200, blank=True, default="")
    status = models.CharField(
        max_length=16, choices=ReleaseStatus.choices, default=ReleaseStatus.PLANNED
    )
    planned_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="releases",
    )

    class Meta(BaseModel.Meta):
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["project", "version"], name="uniq_release_project_version"
            ),
        ]
        # Release lists filter on status; the risk engine's release-proximity signal
        # reads the soonest planned release for a project.
        indexes = [
            models.Index(fields=["project", "status"], name="idx_release_project_status"),
        ]

    def __str__(self) -> str:
        return self.version

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[Release]:
        return cls.objects.filter(project__in=Project.objects.accessible_to(user))


class ReleaseCommitLink(EntityLink):
    """A commit shipped in a release.

    Kept as an explicit edge rather than a commit range because repositories get
    rebased and squashed; the set of commits actually shipped is a fact, not a range.
    """

    release = models.ForeignKey(Release, on_delete=models.CASCADE, related_name="commit_links")
    commit = models.ForeignKey(
        "codebase.Commit", on_delete=models.CASCADE, related_name="release_links"
    )

    class Meta(EntityLink.Meta):
        constraints = [
            models.UniqueConstraint(fields=["release", "commit"], name="uniq_release_commit_link"),
        ]

    def __str__(self) -> str:
        return f"{self.release.version} @ {self.commit.short_sha}"
