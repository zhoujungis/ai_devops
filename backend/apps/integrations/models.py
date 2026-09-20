"""Git connections, repositories and inbound webhook deliveries."""

from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings
from django.db import models

from apps.accounts.models import Organization, Project
from apps.core.models import BaseModel, EncryptedTextField
from apps.core.scoping import ScopedModel


class GitProviderKind(models.TextChoices):
    GITHUB = "github", "GitHub"
    GITLAB = "gitlab", "GitLab"


class GitAuthType(models.TextChoices):
    PAT = "pat", "Personal access token"
    OAUTH = "oauth", "OAuth"
    APP = "app", "App installation"


class ConnectionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INVALID = "invalid", "Invalid credentials"


class GitConnection(BaseModel, ScopedModel):
    """Credentials for one git host, owned by an organization.

    Secrets are stored with :class:`~apps.core.models.EncryptedTextField`, so they
    are unreadable from a database dump. Deleting a connection is refused while
    repositories still reference it.
    """

    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="git_connections")
    provider = models.CharField(
        max_length=32, choices=GitProviderKind.choices, default=GitProviderKind.GITHUB
    )
    auth_type = models.CharField(
        max_length=32, choices=GitAuthType.choices, default=GitAuthType.PAT
    )
    label = models.CharField(max_length=100)
    base_url = models.URLField(blank=True, default="")
    token = EncryptedTextField(blank=True, default="")
    webhook_secret = EncryptedTextField(blank=True, default="")
    status = models.CharField(
        max_length=32, choices=ConnectionStatus.choices, default=ConnectionStatus.ACTIVE
    )
    last_verified_at = models.DateTimeField(null=True, blank=True)
    owner = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="git_connections",
    )

    class Meta(BaseModel.Meta):
        ordering = ("label",)
        constraints = [
            models.UniqueConstraint(fields=["org", "label"], name="uniq_git_connection_org_label"),
        ]

    def __str__(self) -> str:
        return f"{self.label} ({self.provider})"

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[GitConnection]:
        return cls.objects.filter(org__in=Organization.objects.accessible_to(user))


class SyncStatus(models.TextChoices):
    IDLE = "idle", "Idle"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class Repository(BaseModel, ScopedModel):
    """One repository tracked by one project."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="repositories")
    connection = models.ForeignKey(
        GitConnection, on_delete=models.PROTECT, related_name="repositories"
    )
    provider = models.CharField(
        max_length=32, choices=GitProviderKind.choices, default=GitProviderKind.GITHUB
    )
    external_id = models.CharField(max_length=64)
    full_name = models.CharField(max_length=255)
    default_branch = models.CharField(max_length=255, default="main")
    is_private = models.BooleanField(default=False)

    sync_status = models.CharField(
        max_length=32, choices=SyncStatus.choices, default=SyncStatus.IDLE
    )
    sync_cursor = models.JSONField(default=dict, blank=True)
    sync_window_days = models.PositiveIntegerField(default=90)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    sync_error = models.TextField(blank=True, default="")

    # Module resolution is per repository: a monorepo and a single service need
    # different granularity, and overrides beat any heuristic.
    module_depth = models.PositiveSmallIntegerField(default=2)
    module_overrides = models.JSONField(
        default=dict, blank=True, help_text="Path prefix -> module name."
    )

    class Meta(BaseModel.Meta):
        ordering = ("full_name",)
        constraints = [
            models.UniqueConstraint(
                fields=["project", "external_id"], name="uniq_repository_project_external_id"
            ),
        ]

    def __str__(self) -> str:
        return self.full_name

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[Repository]:
        return cls.objects.filter(project__in=Project.objects.accessible_to(user))


class WebhookEvent(BaseModel):
    """Inbound delivery, stored before processing so it can be replayed.

    ``delivery_id`` is unique, which is what makes a re-delivered webhook a no-op
    instead of a duplicate sync.
    """

    connection = models.ForeignKey(
        GitConnection, on_delete=models.CASCADE, related_name="webhook_events"
    )
    delivery_id = models.CharField(max_length=100, unique=True)
    event_type = models.CharField(max_length=64)
    payload = models.JSONField()
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.event_type} {self.delivery_id}"
