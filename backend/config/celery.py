"""Celery application entrypoint."""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("ai_devops_copilot")

# All Celery configuration lives in Django settings under the CELERY_ prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

app.autodiscover_tasks()
