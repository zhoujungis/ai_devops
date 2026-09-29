"""Admin for the risk configuration.

``RiskRule`` is configuration, not code — the whole point is that a team can retune a
weight without a deploy — so it needs somewhere to be edited. There is nothing else to
expose here: a score is computed from live data on demand and is never stored, so
there is no row an operator could usefully edit.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin

from apps.risk.models import RiskRule


@admin.register(RiskRule)
class RiskRuleAdmin(admin.ModelAdmin):
    list_display = ("code", "scope", "weight", "enabled", "updated_by", "updated_at")
    list_filter = ("enabled", "code")
    search_fields = ("code",)
    ordering = ("code",)
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="scope")
    def scope(self, obj: RiskRule) -> str:
        """Where the rule applies: a project beats its org, which beats the default."""
        return str(obj.project or obj.org or "default")

    def save_model(self, request: Any, obj: RiskRule, form: Any, change: bool) -> None:
        # Attributability is the reason `updated_by` exists: every weight change names
        # who made it, so a score that shifts can be traced back to a person.
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
