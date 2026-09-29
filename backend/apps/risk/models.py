"""Risk configuration: the tunable signal weights.

Only the configuration lives here. A score is a function of the current data — storing
one would make it stale the moment anything changed — so it is computed on demand by
:mod:`apps.risk.engine` and never persisted.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings
from django.db import models

from apps.accounts.models import Organization, Project
from apps.core.models import BaseModel


class RiskLevel(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


class RiskRuleManager(models.Manager["RiskRule"]):
    def for_project(self, project: Any) -> list[RiskRule]:
        """Applicable rules, least specific first so the narrowest one wins.

        Three levels: a system default (no org, no project), an organization default,
        and a project override. Applying them in that order means a project rule
        replaces the inherited weight rather than stacking with it.
        """
        rules = list(
            self.get_queryset()
            .filter(enabled=True)
            .filter(
                models.Q(project=project)
                | models.Q(org=project.org, project__isnull=True)
                | models.Q(org__isnull=True, project__isnull=True)
            )
        )
        rules.sort(key=lambda rule: (rule.project_id is not None, rule.org_id is not None))
        return rules


class RiskRule(BaseModel):
    """A tunable signal weight.

    Weights are configuration, not code, so a team can retune risk without a deploy —
    and every change is attributable through ``updated_by``.
    """

    org = models.ForeignKey(
        Organization,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="risk_rules",
    )
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.CASCADE, related_name="risk_rules"
    )
    code = models.CharField(max_length=64)
    weight = models.FloatField()
    enabled = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="risk_rules",
    )

    objects = RiskRuleManager()

    class Meta(BaseModel.Meta):
        ordering = ("code",)
        constraints = [
            models.UniqueConstraint(
                fields=["project", "code"],
                condition=models.Q(project__isnull=False),
                name="uniq_risk_rule_project_code",
            ),
            models.UniqueConstraint(
                fields=["org", "code"],
                condition=models.Q(project__isnull=True, org__isnull=False),
                name="uniq_risk_rule_org_code",
            ),
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(project__isnull=True, org__isnull=True),
                name="uniq_risk_rule_default_code",
            ),
            models.CheckConstraint(
                condition=models.Q(weight__gte=0),
                name="risk_rule_weight_non_negative",
            ),
        ]

    def __str__(self) -> str:
        scope = self.project or self.org or "default"
        return f"{self.code} @ {scope} = {self.weight}"
