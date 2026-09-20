"""Risk configuration and stored assessments.

``RiskSignal`` from the plan is deliberately absent: :attr:`RiskScore.breakdown`
already holds every signal with its raw value, normalisation, weight and
contribution. A parallel table would be the same numbers twice, free to drift.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings
from django.db import models

from apps.accounts.models import Organization, Project
from apps.core.models import BaseModel
from apps.core.scoping import ScopedModel


class RiskSubject(models.TextChoices):
    COMMIT = "commit", "Commit"
    MODULE = "module", "Module"
    RELEASE = "release", "Release"
    PROJECT = "project", "Project"


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
    params = models.JSONField(default=dict, blank=True)
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


class RiskScore(BaseModel, ScopedModel):
    """A stored assessment, kept so risk can be trended rather than re-derived."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="risk_scores")
    subject_type = models.CharField(max_length=16, choices=RiskSubject.choices)
    subject_id = models.CharField(max_length=64)
    score = models.FloatField()
    level = models.CharField(max_length=16, choices=RiskLevel.choices)
    #: The full decomposition: every signal with raw value, normalisation, weight and
    #: contribution. This is what makes a score arguable rather than authoritative.
    breakdown = models.JSONField(default=list, blank=True)
    computed_at = models.DateTimeField()

    class Meta(BaseModel.Meta):
        ordering = ("-computed_at",)
        indexes = [
            models.Index(
                fields=["project", "subject_type", "subject_id", "-computed_at"],
                name="idx_riskscore_lookup",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.subject_type}:{self.subject_id} {self.score:.0f} ({self.level})"

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[RiskScore]:
        return cls.objects.filter(project__in=Project.objects.accessible_to(user))
