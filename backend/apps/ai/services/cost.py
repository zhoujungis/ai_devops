"""Turning token counts into money, or into an honest "we do not know"."""

from __future__ import annotations

from decimal import Decimal

from apps.ai.models import AIModelPricing
from apps.ai.providers.base import TokenUsage

_THOUSAND = Decimal(1000)
_MICRO = Decimal("0.000001")


def compute_cost(*, provider_type: str, model: str, usage: TokenUsage) -> Decimal | None:
    """Cost of one call, or ``None`` when pricing for that model is not recorded.

    ``None`` rather than zero on purpose: an unpriced model must not look free in a
    spend report. Cached tokens are a subset of input tokens, so they are billed at
    their own rate and excluded from the input count rather than added on top.
    """
    pricing = (
        AIModelPricing.objects.filter(provider_type=provider_type, model=model)
        .order_by("-effective_from")
        .first()
    )
    if pricing is None:
        return None

    cached = min(usage.cached_tokens, usage.input_tokens)
    billable_input = usage.input_tokens - cached

    total = (
        Decimal(billable_input) / _THOUSAND * pricing.input_per_1k
        + Decimal(cached) / _THOUSAND * pricing.cached_input_per_1k
        + Decimal(usage.output_tokens) / _THOUSAND * pricing.output_per_1k
    )
    return total.quantize(_MICRO)
