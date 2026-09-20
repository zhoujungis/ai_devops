"""Bugs, their occurrences, and the evidence linking them to code and tests."""

from __future__ import annotations

from django.apps import AppConfig


class BugsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.bugs"
    label = "bugs"
    verbose_name = "Bugs"
