"""Admin for the AI tables an operator is meant to touch.

Two registrations, for two different reasons:

* :class:`~apps.ai.models.AIModelPricing` is *configuration*, like the risk weights —
  vendor prices change, and a price row has to be added without a deploy or the cost
  column is permanently ``NULL``;
* :class:`~apps.ai.models.AuditLog` is **read-only** — an audit log that can be edited
  through the admin is not an audit log.

Model credentials are deliberately absent: ``AIProviderConfig.api_key`` is decrypted on
read, so putting it in a form would print the secret on screen.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin

from apps.ai.models import AIModelPricing, AuditLog

_FIELDS = (
    "id",
    "org",
    "actor_type",
    "actor_id",
    "action",
    "target_type",
    "target_id",
    "before",
    "after",
    "request_id",
    "ip",
    "created_at",
    "updated_at",
)


@admin.register(AIModelPricing)
class AIModelPricingAdmin(admin.ModelAdmin):
    """Reference prices. A row is added, not edited in place, when a vendor re-prices."""

    list_display = (
        "provider_type",
        "model",
        "input_per_1k",
        "cached_input_per_1k",
        "output_per_1k",
        "currency",
        "effective_from",
    )
    list_filter = ("provider_type", "currency")
    search_fields = ("model",)
    ordering = ("provider_type", "model", "-effective_from")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor_type", "action", "target_type", "target_id", "request_id")
    list_filter = ("actor_type", "action")
    search_fields = ("action", "target_type", "target_id", "request_id")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    readonly_fields = _FIELDS

    def has_add_permission(self, request: Any) -> bool:
        return False

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False
