"""Serializer layer for git connections and repositories.

Secrets are write-only: ``token`` and ``webhook_secret`` can be set but are never
echoed back. ``has_token`` exists so a client can show state without the value.
"""

from __future__ import annotations

from typing import Any

from django.urls import reverse
from rest_framework import serializers

from apps.integrations.models import GitConnection, Repository


class WebhookAcceptedSerializer(serializers.Serializer):
    """What we tell the git host after a delivery."""

    status = serializers.CharField()
    event_id = serializers.CharField(required=False)


class GitConnectionSerializer(serializers.ModelSerializer):
    token = serializers.CharField(write_only=True, required=False, allow_blank=True)
    webhook_secret = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_token = serializers.SerializerMethodField()
    webhook_url = serializers.SerializerMethodField()

    class Meta:
        model = GitConnection
        fields = (
            "id",
            "org",
            "provider",
            "auth_type",
            "label",
            "base_url",
            "status",
            "owner",
            "created_at",
            "last_verified_at",
            "token",
            "webhook_secret",
            "has_token",
            "webhook_url",
        )
        read_only_fields = ("id", "org", "owner", "status", "created_at", "last_verified_at")

    def get_has_token(self, obj: GitConnection) -> bool:
        return bool(obj.token)

    def get_webhook_url(self, obj: GitConnection) -> str:
        """Where to point the provider's webhook settings."""
        path = reverse("github-webhook", kwargs={"connection_pk": obj.pk})
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request is not None else path


class RepositorySerializer(serializers.ModelSerializer):
    """Read/update view of a tracked repository."""

    # Same trap as ProjectSerializer.org: reading the relation would call `str()` on
    # the connection and return its label, not its id. Read the id explicitly.
    connection = serializers.UUIDField(source="connection_id", read_only=True)

    class Meta:
        model = Repository
        fields = (
            "id",
            "project",
            "connection",
            "provider",
            "external_id",
            "full_name",
            "default_branch",
            "is_private",
            "sync_status",
            "sync_window_days",
            "sync_error",
            "last_synced_at",
            "module_depth",
            "module_overrides",
            "created_at",
        )
        read_only_fields = (
            "id",
            "project",
            "connection",
            "provider",
            "external_id",
            "full_name",
            "default_branch",
            "is_private",
            "sync_status",
            "sync_error",
            "last_synced_at",
            "created_at",
        )


class RepositoryCreateSerializer(serializers.Serializer):
    """Registering a repository only needs a connection and its full name.

    Everything else — external id, default branch, visibility — is resolved from
    the provider rather than trusted from the client.
    """

    connection = serializers.PrimaryKeyRelatedField(queryset=GitConnection.objects.all())
    full_name = serializers.CharField(max_length=255)
    sync_window_days = serializers.IntegerField(required=False, min_value=1, max_value=3650)
    module_depth = serializers.IntegerField(required=False, min_value=1, max_value=8)
    module_overrides = serializers.DictField(
        child=serializers.CharField(), required=False, allow_empty=True
    )

    def validate_full_name(self, value: str) -> str:
        cleaned = value.strip().strip("/")
        if cleaned.count("/") != 1 or not all(cleaned.split("/")):
            raise serializers.ValidationError("Expected the form 'owner/repository'.")
        return cleaned

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        project = self.context.get("project")
        connection = attrs["connection"]
        if project is not None and connection.org_id != project.org_id:
            # Otherwise a caller could point a project at another tenant's credentials.
            raise serializers.ValidationError(
                {"connection": "This connection belongs to another organization."}
            )
        return attrs
