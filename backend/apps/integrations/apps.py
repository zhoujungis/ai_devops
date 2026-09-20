"""Git host connections, repository sync and inbound webhooks."""

from __future__ import annotations

from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integrations"
    label = "integrations"
    verbose_name = "Integrations"
