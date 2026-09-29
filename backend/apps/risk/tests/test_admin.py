"""The risk-rule admin: the one place a weight is meant to be changed by hand."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from django.contrib import admin

from apps.accounts.models import Organization, Project
from apps.accounts.tests.factories import (
    MembershipFactory,
    OrganizationFactory,
    ProjectFactory,
    UserFactory,
)
from apps.risk.admin import RiskRuleAdmin
from apps.risk.models import RiskRule

pytestmark = pytest.mark.django_db


def test_risk_rule_is_registered_in_the_admin() -> None:
    """Without this, "retune without a deploy" is unreachable configuration."""
    assert admin.site.is_registered(RiskRule)


def test_saving_a_rule_attributes_the_change() -> None:
    org = cast(Organization, OrganizationFactory())
    project = cast(Project, ProjectFactory(org=org))
    user = UserFactory()
    MembershipFactory(org=org, user=user, role="admin")

    model_admin = RiskRuleAdmin(RiskRule, admin.site)
    rule = RiskRule(org=org, project=project, code="change_volume", weight=20.0)
    model_admin.save_model(cast(Any, SimpleNamespace(user=user)), rule, cast(Any, None), False)

    rule.refresh_from_db()
    assert rule.weight == 20.0
    assert rule.updated_by == user


def test_the_scope_column_names_the_level_a_rule_applies_at() -> None:
    org = cast(Organization, OrganizationFactory())
    project = cast(Project, ProjectFactory(org=org))
    model_admin = RiskRuleAdmin(RiskRule, admin.site)

    assert model_admin.scope(RiskRule(code="change_volume", weight=15.0)) == "default"
    assert model_admin.scope(RiskRule(org=org, code="change_volume", weight=15.0)) == str(org)
    assert model_admin.scope(RiskRule(project=project, code="change_volume", weight=15.0)) == str(
        project
    )
