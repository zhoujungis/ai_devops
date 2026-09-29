"""Test case, suite and run serializers."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from rest_framework import serializers

from apps.core.models import LinkSource
from apps.core.serializers import ProjectScopedUniqueMixin
from apps.testing.models import (
    CoverageSnapshot,
    TestCase,
    TestCaseCommitLink,
    TestCaseStep,
    TestResult,
    TestResultStatus,
    TestRun,
    TestSuite,
)


class TestCaseStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestCaseStep
        fields = ("id", "seq", "action", "test_data")
        read_only_fields = ("id",)


class TestCaseSerializer(ProjectScopedUniqueMixin, serializers.ModelSerializer):
    """A case is written together with its steps.

    ``steps`` replaces the set rather than merging, the same way a requirement's items
    do: a partially merged procedure is one nobody can trust.
    """

    steps = TestCaseStepSerializer(many=True, required=False)
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

    @transaction.atomic
    def create(self, validated_data: dict[str, Any]) -> TestCase:
        steps = validated_data.pop("steps", [])
        test_case = super().create(validated_data)
        self._replace_steps(test_case, steps)
        return test_case

    @transaction.atomic
    def update(self, instance: TestCase, validated_data: dict[str, Any]) -> TestCase:
        steps = validated_data.pop("steps", None)
        test_case = super().update(instance, validated_data)
        if steps is not None:
            self._replace_steps(test_case, steps)
        return test_case

    @staticmethod
    def _replace_steps(test_case: TestCase, steps: list[dict[str, Any]]) -> None:
        # Delete-then-insert must be atomic: a failed bulk_create would otherwise leave
        # the case with no procedure at all.
        test_case.steps.all().delete()
        TestCaseStep.objects.bulk_create(
            [
                TestCaseStep(
                    test_case=test_case,
                    seq=step.get("seq", index + 1),
                    action=step["action"],
                    test_data=step.get("test_data") or {},
                )
                for index, step in enumerate(steps)
            ]
        )


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
    """A run is written together with its results.

    The header counters are recomputed from the results whenever results are supplied,
    so a run cannot claim 42 passed while its own results say otherwise.
    """

    results = TestResultSerializer(many=True, required=False)
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

    def validate_results(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        project = self.context.get("project")
        if project is None:
            return value
        for row in value:
            test_case = row.get("test_case")
            if test_case is not None and test_case.project_id != project.pk:
                raise serializers.ValidationError(
                    "A result may only reference a test case in this project."
                )
        return value

    @transaction.atomic
    def create(self, validated_data: dict[str, Any]) -> TestRun:
        results = validated_data.pop("results", [])
        run = super().create(validated_data)
        self._replace_results(run, results)
        self._recount(run)
        self._link_verified_cases(run, results)
        return run

    @transaction.atomic
    def update(self, instance: TestRun, validated_data: dict[str, Any]) -> TestRun:
        results = validated_data.pop("results", None)
        run = super().update(instance, validated_data)
        if results is not None:
            self._replace_results(run, results)
            self._recount(run)
            self._link_verified_cases(run, results)
        return run

    @staticmethod
    def _replace_results(run: TestRun, results: list[dict[str, Any]]) -> None:
        run.results.all().delete()
        TestResult.objects.bulk_create([TestResult(run=run, **row) for row in results])

    @staticmethod
    def _link_verified_cases(run: TestRun, results: list[dict[str, Any]]) -> None:
        """A passing case in a run tied to a commit has verified that commit.

        That edge is what makes the correlation engine's "this test exercised the same
        area before" evidence real; without a writer for it the weight could never
        apply, and the regression ranking would use module membership alone.
        """
        if run.commit_id is None:
            return
        test_case_ids = [
            row["test_case"].pk
            for row in results
            if row.get("test_case") is not None and row.get("status") == TestResultStatus.PASSED
        ]
        if not test_case_ids:
            return
        TestCaseCommitLink.objects.bulk_create(
            [
                TestCaseCommitLink(
                    test_case_id=test_case_id,
                    commit_id=run.commit_id,
                    source=LinkSource.INFERRED,
                    confidence=0.75,
                )
                for test_case_id in test_case_ids
            ],
            ignore_conflicts=True,
        )

    @staticmethod
    def _recount(run: TestRun) -> None:
        statuses = list(run.results.values_list("status", flat=True))
        if not statuses:
            return
        run.total = len(statuses)
        run.passed = statuses.count("passed")
        run.failed = statuses.count("failed") + statuses.count("error")
        run.skipped = statuses.count("skipped")
        run.save(update_fields=["total", "passed", "failed", "skipped", "updated_at"])


class CoverageSnapshotSerializer(serializers.ModelSerializer):
    """A coverage measurement. Facts are appended, not edited."""

    class Meta:
        model = CoverageSnapshot
        fields = (
            "id",
            "project",
            "commit",
            "module",
            "line_rate",
            "branch_rate",
            "source",
            "captured_at",
        )
        read_only_fields = ("id", "project")

    def validate_module(self, value: Any) -> Any:
        project = self.context.get("project")
        if value is not None and project is not None and value.project_id != project.pk:
            raise serializers.ValidationError("This module belongs to another project.")
        return value

    def validate_commit(self, value: Any) -> Any:
        project = self.context.get("project")
        if (
            value is not None
            and project is not None
            and value.repository.project_id != project.pk
        ):
            raise serializers.ValidationError("This commit belongs to another project.")
        return value
