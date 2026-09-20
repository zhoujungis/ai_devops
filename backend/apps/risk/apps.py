"""Risk scoring: measured signals in, an explainable score out."""

from __future__ import annotations

from django.apps import AppConfig


class RiskConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.risk"
    label = "risk"
    verbose_name = "Risk"
