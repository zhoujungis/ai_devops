"""The audit trail is readable in the admin and editable nowhere.

Model prices are the opposite: they are configuration, and without a place to enter
them no run ever gets a cost.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from django.contrib import admin

from apps.ai.admin import AuditLogAdmin
from apps.ai.models import AIModelPricing, AuditLog


class _Superuser:
    """Enough of a request user for Django's default permission checks."""

    def has_perm(self, perm: str) -> bool:
        return True


pytestmark = pytest.mark.django_db


def test_the_audit_log_is_registered() -> None:
    assert admin.site.is_registered(AuditLog)


def test_the_audit_log_cannot_be_written_through_the_admin() -> None:
    model_admin = AuditLogAdmin(AuditLog, admin.site)
    request = cast(Any, SimpleNamespace(user=None))

    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request) is False
    assert model_admin.has_delete_permission(request) is False


def test_model_pricing_is_registered_and_editable() -> None:
    """Cost accounting is inert without a way to record a price."""
    assert admin.site.is_registered(AIModelPricing)
    model_admin = cast(Any, admin.site._registry[AIModelPricing])
    request = cast(Any, SimpleNamespace(user=_Superuser()))

    assert model_admin.has_add_permission(request) is True
    assert model_admin.has_change_permission(request) is True
