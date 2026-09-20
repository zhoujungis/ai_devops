"""Test cases, suites, runs and how they connect to code and requirements."""

from __future__ import annotations

from django.apps import AppConfig


class TestingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.testing"
    label = "testing"
    verbose_name = "Testing"
