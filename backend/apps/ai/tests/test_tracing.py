"""Tracing and cost accounting.

The point of these tables is that an answer can be audited afterwards, so the tests
assert that the audit trail is complete rather than merely present.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

import pytest

from apps.accounts.models import Organization, Project
from apps.accounts.tests.factories import (
    MembershipFactory,
    OrganizationFactory,
    ProjectFactory,
    UserFactory,
)
from apps.ai.models import AIModelPricing, ToolCallStatus
from apps.ai.providers.base import ChatResult, TokenUsage
from apps.ai.services.cost import compute_cost
from apps.ai.services.tracing import record_run, record_tool_call
from apps.ai.tasks import create_job

pytestmark = pytest.mark.django_db


@pytest.fixture
def project(db: Any) -> Project:
    org = cast(Organization, OrganizationFactory())
    MembershipFactory(org=org, user=UserFactory(), role="admin")
    return cast(Project, ProjectFactory(org=org))


def _pricing(**overrides: Any) -> AIModelPricing:
    defaults: dict[str, Any] = {
        "provider_type": "openai",
        "model": "gpt-4o",
        "input_per_1k": Decimal("0.0025"),
        "output_per_1k": Decimal("0.010"),
        "cached_input_per_1k": Decimal("0.00125"),
        "effective_from": "2026-01-01T00:00:00Z",
    }
    defaults.update(overrides)
    return AIModelPricing.objects.create(**defaults)


# ---------------------------------------------------------------------------
# cost
# ---------------------------------------------------------------------------
def test_cost_is_computed_from_the_price_table() -> None:
    _pricing()

    cost = compute_cost(
        provider_type="openai",
        model="gpt-4o",
        usage=TokenUsage(input_tokens=1000, output_tokens=500),
    )

    assert cost == Decimal("0.0025") + Decimal("0.005")


def test_cached_tokens_are_billed_at_their_own_rate_not_twice() -> None:
    _pricing()

    cost = compute_cost(
        provider_type="openai",
        model="gpt-4o",
        usage=TokenUsage(input_tokens=1000, output_tokens=0, cached_tokens=800),
    )

    # 200 fresh at 0.0025/1k plus 800 cached at 0.00125/1k.
    assert cost == Decimal("0.0005") + Decimal("0.001")


def test_an_unpriced_model_reports_unknown_rather_than_free() -> None:
    cost = compute_cost(
        provider_type="openai", model="some-new-model", usage=TokenUsage(input_tokens=1000)
    )

    assert cost is None


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------
def test_a_run_records_everything_needed_to_audit_it(project: Project) -> None:
    _pricing()
    job, _ = create_job(project=project, agent_code="code_impact", target_type="commit")

    run = record_run(
        job=job,
        provider_type="openai",
        model="gpt-4o",
        capability="chat",
        input_text="the rendered prompt",
        prompt_id="code_impact",
        prompt_version=1,
        result=ChatResult(
            content='{"summary": "ok"}',
            model="gpt-4o-2024-08-06",
            usage=TokenUsage(input_tokens=1000, output_tokens=200),
            latency_ms=1234,
        ),
    )

    assert run.prompt_id == "code_impact"
    assert run.prompt_version == 1
    assert run.model == "gpt-4o-2024-08-06"
    assert run.input_tokens == 1000
    assert run.output_tokens == 200
    assert run.latency_ms == 1234
    assert run.cost_usd is not None
    assert run.output_text == '{"summary": "ok"}'
    assert len(run.input_digest) == 64


def test_the_input_digest_changes_with_the_input(project: Project) -> None:
    job, _ = create_job(project=project, agent_code="x")

    first = record_run(
        job=job, provider_type="openai", model="m", capability="chat", input_text="a"
    )
    second = record_run(
        job=job, provider_type="openai", model="m", capability="chat", input_text="b"
    )

    assert first.input_digest != second.input_digest


def test_a_failed_run_keeps_its_error(project: Project) -> None:
    job, _ = create_job(project=project, agent_code="x")

    run = record_run(
        job=job,
        provider_type="openai",
        model="m",
        capability="chat",
        input_text="p",
        error="AIRateLimitError: slow down",
    )

    assert run.status == "failed"
    assert "slow down" in run.error
    assert run.cost_usd is None


def test_a_huge_input_is_truncated_but_still_identifiable(project: Project, settings: Any) -> None:
    settings.AI_TRACE_TEXT_MAX_BYTES = 100
    job, _ = create_job(project=project, agent_code="x")

    run = record_run(
        job=job,
        provider_type="openai",
        model="m",
        capability="chat",
        input_text="x" * 5000,
    )

    assert run.input_truncated is True
    assert len(run.input_text) == 100
    assert len(run.input_digest) == 64


# ---------------------------------------------------------------------------
# tool calls
# ---------------------------------------------------------------------------
def test_tool_calls_are_sequenced_within_a_run(project: Project) -> None:
    job, _ = create_job(project=project, agent_code="x")
    run = record_run(job=job, provider_type="openai", model="m", capability="chat", input_text="p")

    first = record_tool_call(
        run=run, tool_name="get_commit", arguments={"sha": "a"}, result_summary={"ok": True}
    )
    second = record_tool_call(
        run=run,
        tool_name="search_bug",
        arguments={"query": "x"},
        status=ToolCallStatus.DENIED,
        scope_denied=True,
        error="outside project",
    )

    assert (first.sequence, second.sequence) == (1, 2)
    assert second.scope_denied is True
    assert second.status == ToolCallStatus.DENIED


def test_run_sequences_increment_within_a_job(project: Project) -> None:
    job, _ = create_job(project=project, agent_code="x")

    runs = [
        record_run(job=job, provider_type="openai", model="m", capability="chat", input_text=str(i))
        for i in range(3)
    ]

    assert [run.sequence for run in runs] == [1, 2, 3]
