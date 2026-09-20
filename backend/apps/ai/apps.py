"""AI layer: providers, prompts, schemas, tools, agents and their traces."""

from __future__ import annotations

from django.apps import AppConfig


class AiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ai"
    label = "ai"
    verbose_name = "AI"
