"""Code entities: branches, commits, changed files and resolved modules."""

from __future__ import annotations

from django.apps import AppConfig


class CodebaseConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.codebase"
    label = "codebase"
    verbose_name = "Codebase"
