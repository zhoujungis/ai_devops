"""Test case, suite and run serializers."""

from __future__ import annotations

from rest_framework import serializers

from apps.core.serializers import ProjectScopedUniqueMixin
from apps.testing.models import (
    TestCase,
    TestCaseStep,
    TestResult,
    TestRun,
    TestSuite,
)


class TestCaseStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestCaseStep
        fields = ("id", "seq", "action", "test_data")
        read_only_fields = ("id",)


class TestCaseSerializer(ProjectScopedUniqueMixin, serializers.ModelSerializer):
    steps = TestCaseStepSerializer(many=True, read_only=True)
    module_paths = serializers.SerializerMethodField()

    unique_fields = ("key",)

    class Meta:
        model = TestCase
        fields = (
            "id",
            "project",
            "key",
            "title",
            "precondition",
            "expected",
            "priority",
            "tags",
            "automation",
            "automation_suggestion",
            "origin",
            "status",
            "version",
            "steps",
            "module_paths",
            "created_at",
        )
        read_only_fields = ("id", "project", "module_paths", "created_at")

    def get_module_paths(self, obj: TestCase) -> list[str]:
        """Which modules this case covers, straight from the link table."""
        return sorted(link.module.path_prefix for link in obj.module_links.all())

    def validate_key(self, value: str) -> str:
        return value.strip().upper()


class TestSuiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestSuite
        fields = ("id", "project", "name", "kind", "description", "created_at")
        read_only_fields = ("id", "project", "created_at")


class TestResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestResult
        fields = (
            "id",
            "test_case",
            "case_key",
            "status",
            "duration_ms",
            "error_message",
            "stack_trace",
            "attachments",
        )


class TestRunSerializer(serializers.ModelSerializer):
    results = TestResultSerializer(many=True, read_only=True)
    failure_count = serializers.SerializerMethodField()

    class Meta:
        model = TestRun
        fields = (
            "id",
            "project",
            "suite",
            "environment",
            "trigger",
            "commit",
            "status",
            "started_at",
            "finished_at",
            "total",
            "passed",
            "failed",
            "skipped",
            "results",
            "failure_count",
            "created_at",
        )
        read_only_fields = ("id", "project", "created_at")

    def get_failure_count(self, obj: TestRun) -> int:
        return obj.failed
