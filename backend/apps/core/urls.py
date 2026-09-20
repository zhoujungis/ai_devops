"""Versioned API routes owned by the core app.

Feature apps mount their routers under ``/api/v1/`` as they are added.
"""

from __future__ import annotations

from django.urls import URLPattern

urlpatterns: list[URLPattern] = []
