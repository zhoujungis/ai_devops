"""Serializers for the AI surface: jobs, findings, recommendations, providers."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.ai.agents.base import AGENTS
from apps.ai.models import (
    AIAnalysisJob,
    AIAnalysisRun,
    AIFinding,
    AIProviderConfig,
    AIRecommendation,
    AIToolCall,
    AuditLog,
)


class AnalysisRequestSerializer(serializers.Serializer):
    """What a client sends to start an analysis.

    ``idempotency_key`` exists so a client that times out and retries is not charged
    for the same analysis twice.
    """

    agent = serializers.CharField(max_length=64)
    target_type = serializers.CharField(max_length=64, required=False, allow_blank=True)
    target_id = serializers.CharField(max_length=64, required=False, allow_blank=True)
    params = serializers.JSONField(required=False)
    idempotency_key = serializers.CharField(
        max_length=128, required=False, allow_blank=True, default=""
    )

    def validate_agent(self, value: str) -> str:
        if value not in AGENTS:
            raise serializers.ValidationError(
                f"Unknown agent {value!r}. Available: {', '.join(sorted(AGENTS)) or '(none)'}"
            )
        return value


class AnalysisRunSerializer(serializers.ModelSerializer):
    """The audit trail for one model interaction inside a job."""

    class Meta:
        model = AIAnalysisRun
        fields = (
            "id",
            "sequence",
            "provider_type",
            "model",
            "capability",
            "prompt_id",
            "prompt_version",
            "input_digest",
            "input_truncated",
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "latency_ms",
            "cost_usd",
            "status",
            "error",
        )
        read_only_fields = fields


class AnalysisJobSerializer(serializers.ModelSerializer):
    usage = serializers.SerializerMethodField()
    findings = serializers.SerializerMethodField()

    class Meta:
        model = AIAnalysisJob
        fields = (
            "id",
            "project",
            "agent_code",
            "target_type",
            "target_id",
            "params",
            "status",
            "progress",
            "error",
            "result",
            "requested_by",
            "celery_task_id",
            "started_at",
            "finished_at",
            "created_at",
            "usage",
            "findings",
        )
        read_only_fields = fields

    def get_usage(self, obj: AIAnalysisJob) -> dict[str, Any]:
        """Tokens and cost rolled up across the job's runs, so a client can show spend."""
        totals = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
        cost = None
        for run in obj.runs.all():
            totals["input_tokens"] += run.input_tokens
            totals["output_tokens"] += run.output_tokens
            totals["cached_tokens"] += run.cached_tokens
            if run.cost_usd is not None:
                cost = (cost or 0) + run.cost_usd
        return {**totals, "cost_usd": cost, "runs": obj.runs.count()}

    def get_findings(self, obj: AIAnalysisJob) -> list[str]:
        return [str(finding.pk) for finding in obj.findings.all()]


class ToolCallSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIToolCall
        fields = (
            "id",
            "sequence",
            "tool_name",
            "arguments",
            "result_summary",
            "status",
            "duration_ms",
            "scope_denied",
            "error",
        )
        read_only_fields = fields


class AIFindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIFinding
        fields = (
            "id",
            "project",
            "job",
            "agent_code",
            "severity",
            "category",
            "title",
            "summary",
            "payload",
            "confidence",
            "evidence",
            "status",
            "created_at",
        )
        read_only_fields = fields


class AIRecommendationSerializer(serializers.ModelSerializer):
    confirmation = serializers.SerializerMethodField()

    class Meta:
        model = AIRecommendation
        fields = (
            "id",
            "project",
            "job",
            "finding",
            "agent_code",
            "type",
            "title",
            "description",
            "payload",
            "risk_level",
            "status",
            "expires_at",
            "created_at",
            "confirmation",
        )
        read_only_fields = fields

    def get_confirmation(self, obj: AIRecommendation) -> dict[str, Any] | None:
        confirmation = getattr(obj, "confirmation", None)
        if confirmation is None:
            return None
        return {
            "decision": confirmation.decision,
            "reason": confirmation.reason,
            "executor_code": confirmation.executor_code,
            "result": confirmation.result,
            "executed_at": confirmation.executed_at,
        }


class RecommendationDecisionSerializer(serializers.Serializer):
    """The body of a confirm or reject call."""

    edited_payload = serializers.JSONField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class AIProviderConfigSerializer(serializers.ModelSerializer):
    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_api_key = serializers.SerializerMethodField()

    class Meta:
        model = AIProviderConfig
        fields = (
            "id",
            "org",
            "provider_type",
            "label",
            "base_url",
            "capability_models",
            "embedding_dim",
            "is_default",
            "status",
            "last_verified_at",
            "created_at",
            "api_key",
            "has_api_key",
        )
        read_only_fields = ("id", "org", "status", "last_verified_at", "created_at", "has_api_key")

    def get_has_api_key(self, obj: AIProviderConfig) -> bool:
        return bool(obj.api_key)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        capability_models = attrs.get("capability_models")
        if capability_models is not None and not isinstance(capability_models, dict):
            raise serializers.ValidationError(
                {"capability_models": "Expected an object mapping capability to model name."}
            )
        return attrs


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = (
            "id",
            "actor_type",
            "actor_id",
            "action",
            "target_type",
            "target_id",
            "before",
            "after",
            "request_id",
            "created_at",
        )
        read_only_fields = fields
