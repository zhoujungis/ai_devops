"""Tenancy models: users, organizations, projects and their memberships.

The tenancy hierarchy is ``Organization -> Project -> data``. Every future domain
model hangs off an organization or a project, so access can always be narrowed by
joining back to :class:`Membership` or :class:`ProjectMembership`.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, ClassVar

from django.conf import settings as django_settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.text import slugify

from apps.accounts.managers import UserManager
from apps.accounts.roles import Role, coerce, highest
from apps.core.models import BaseModel
from apps.core.scoping import ScopedModel

if TYPE_CHECKING:
    from django.db.models import QuerySet


class User(AbstractUser):
    """Email-identified user.

    Organizations own everything in the system; a user only ever reaches data
    through a membership.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = None  # type: ignore[assignment]
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=150, blank=True)
    avatar_url = models.URLField(blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    objects = UserManager()  # type: ignore[misc,assignment]

    class Meta:
        ordering = ("email",)
        verbose_name = "user"
        verbose_name_plural = "users"

    def __str__(self) -> str:
        return self.display_name or self.email

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Emails are compared case-insensitively by every lookup path in the app.
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)


class OrganizationManager(models.Manager["Organization"]):
    def accessible_to(self, user: Any) -> QuerySet[Organization]:
        """Organizations the user is a member of."""
        return self.get_queryset().filter(memberships__user=user).distinct()


class Organization(BaseModel, ScopedModel):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True, blank=True)
    plan = models.CharField(max_length=50, default="free")
    settings = models.JSONField(default=dict, blank=True)

    objects = OrganizationManager()

    class Meta(BaseModel.Meta):
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self) -> str:
        base = slugify(self.name)[:180] or "org"
        candidate = base
        suffix = 2
        siblings = Organization.objects.exclude(pk=self.pk)
        while siblings.filter(slug=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def role_for(self, user: Any) -> Role | None:
        """The user's role in this organization, or ``None`` if not a member."""
        if not getattr(user, "is_authenticated", False):
            return None
        # List endpoints prefetch the caller's membership onto ``viewer_memberships``
        # so role resolution is one query per page rather than one per row. The
        # attribute is only ever set for the requesting user, and the serializers
        # only ever ask about that same user.
        prefetched = getattr(self, "viewer_memberships", None)
        if prefetched is not None:
            return coerce(prefetched[0].role) if prefetched else None
        membership = self.memberships.filter(user=user).only("role").first()
        return coerce(membership.role) if membership is not None else None

    @classmethod
    def scoped_for(cls, user: Any) -> QuerySet[Organization]:
        return cls.objects.accessible_to(user)


class Membership(BaseModel, ScopedModel):
    """A user's baseline role inside one organization."""

    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="org_memberships",
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)

    class Meta(BaseModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["org", "user"], name="uniq_membership_org_user"),
        ]

    def __str__(self) -> str:
        return f"{self.user} @ {self.org} ({self.role})"

    @classmethod
    def scoped_for(cls, user: Any) -> QuerySet[Membership]:
        return cls.objects.filter(org__in=Organization.objects.accessible_to(user))


class ProjectStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class ProjectManager(models.Manager["Project"]):
    def accessible_to(self, user: Any) -> QuerySet[Project]:
        """Projects reachable through organization membership or a direct grant."""
        return (
            self.get_queryset()
            .filter(models.Q(org__memberships__user=user) | models.Q(memberships__user=user))
            .distinct()
        )


class Project(BaseModel, ScopedModel):
    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="projects")
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    # Short stable prefix used in human-readable keys (PAY-18, TC-001).
    # The default matters: DRF forces `required=True` on any field that takes part
    # in a unique constraint unless it has a default or is nullable, and a prefix
    # is genuinely optional.
    key_prefix = models.CharField(max_length=10, blank=True, default="")
    status = models.CharField(
        max_length=20, choices=ProjectStatus.choices, default=ProjectStatus.ACTIVE
    )
    settings = models.JSONField(default=dict, blank=True)

    objects = ProjectManager()

    class Meta(BaseModel.Meta):
        ordering = ("name",)
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["org", "slug"], name="uniq_project_org_slug"),
            models.UniqueConstraint(
                fields=["org", "key_prefix"],
                condition=~models.Q(key_prefix=""),
                name="uniq_project_org_key_prefix",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.org.slug}/{self.slug}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self) -> str:
        base = slugify(self.name)[:180] or "project"
        candidate = base
        suffix = 2
        siblings = Project.objects.filter(org=self.org).exclude(pk=self.pk)
        while siblings.filter(slug=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def role_for(self, user: Any) -> Role | None:
        """Effective role: the higher of the organization and project roles.

        An organization membership is the baseline for every project; a project
        membership can elevate it but never reduce it.
        """
        if not getattr(user, "is_authenticated", False):
            return None
        # See Organization.role_for: the prefetched attribute avoids a query per
        # project, and the organization's baseline role is prefetched alongside it.
        prefetched = getattr(self, "viewer_project_memberships", None)
        if prefetched is not None:
            project_role = prefetched[0].role if prefetched else None
        else:
            project_membership = self.memberships.filter(user=user).only("role").first()
            project_role = project_membership.role if project_membership is not None else None
        return highest(self.org.role_for(user), project_role)

    @classmethod
    def scoped_for(cls, user: Any) -> QuerySet[Project]:
        return cls.objects.accessible_to(user)


class ProjectMembership(BaseModel, ScopedModel):
    """An optional per-project role that elevates the organization role."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_memberships",
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)

    class Meta(BaseModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["project", "user"], name="uniq_project_membership_project_user"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} @ {self.project} ({self.role})"

    @classmethod
    def scoped_for(cls, user: Any) -> QuerySet[ProjectMembership]:
        return cls.objects.filter(project__in=Project.objects.accessible_to(user))
