"""Test cases, suites, runs and results."""

from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings
from django.db import models
from pgvector.django import VectorField

from apps.accounts.models import Project
from apps.core.enums import Priority
from apps.core.models import BaseModel, EntityLink
from apps.core.scoping import ScopedModel


class TestCaseOrigin(models.TextChoices):
    MANUAL = "manual", "Written by hand"
    AI_GENERATED = "ai_generated", "Suggested by AI"


class TestCaseStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    DEPRECATED = "deprecated", "Deprecated"


class AutomationKind(models.TextChoices):
    MANUAL = "manual", "Manual"
    API = "api", "API"
    UI = "ui", "UI"
    UNIT = "unit", "Unit"


class TestCase(BaseModel, ScopedModel):
    """A single test case, addressed by the human key TC-001."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="test_cases")
    key = models.CharField(max_length=64)
    title = models.CharField(max_length=500)
    precondition = models.TextField(blank=True, default="")
    expected = models.TextField(blank=True, default="")
    priority = models.CharField(max_length=4, choices=Priority.choices, default=Priority.P2)
    tags = models.JSONField(default=list, blank=True)
    automation = models.CharField(
        max_length=16, choices=AutomationKind.choices, default=AutomationKind.MANUAL
    )
    #: Shape of a generated automation sketch; empty for hand-written cases.
    automation_suggestion = models.JSONField(default=dict, blank=True)
    origin = models.CharField(
        max_length=16, choices=TestCaseOrigin.choices, default=TestCaseOrigin.MANUAL
    )
    status = models.CharField(
        max_length=16, choices=TestCaseStatus.choices, default=TestCaseStatus.ACTIVE
    )
    version = models.PositiveIntegerField(default=1)
    #: Written when embeddings are computed (S7); used to deduplicate generated cases.
    embedding = VectorField(dimensions=django_settings.EMBEDDING_DIM, null=True, blank=True)
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="test_cases",
    )

    class Meta(BaseModel.Meta):
        ordering = ("key",)
        constraints = [
            models.UniqueConstraint(fields=["project", "key"], name="uniq_testcase_project_key"),
        ]
        indexes = [
            models.Index(fields=["project", "status"], name="idx_testcase_project_status"),
        ]

    def __str__(self) -> str:
        return f"{self.key} {self.title}"

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[TestCase]:
        return cls.objects.filter(project__in=Project.objects.accessible_to(user))


class TestCaseStep(BaseModel):
    test_case = models.ForeignKey(TestCase, on_delete=models.CASCADE, related_name="steps")
    seq = models.PositiveSmallIntegerField(default=1)
    action = models.TextField()
    test_data = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ("test_case", "seq")
        constraints = [
            models.UniqueConstraint(fields=["test_case", "seq"], name="uniq_testcase_step_seq"),
        ]

    def __str__(self) -> str:
        return f"{self.test_case.key}#{self.seq}"


class TestSuiteKind(models.TextChoices):
    REGRESSION = "regression", "Regression"
    SMOKE = "smoke", "Smoke"
    FULL = "full", "Full"
    MODULE = "module", "Module"


class TestSuite(BaseModel, ScopedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="test_suites")
    name = models.CharField(max_length=200)
    kind = models.CharField(
        max_length=16, choices=TestSuiteKind.choices, default=TestSuiteKind.REGRESSION
    )
    description = models.TextField(blank=True, default="")

    class Meta(BaseModel.Meta):
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=["project", "name"], name="uniq_testsuite_project_name"),
        ]

    def __str__(self) -> str:
        return self.name

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[TestSuite]:
        return cls.objects.filter(project__in=Project.objects.accessible_to(user))


class TestSuiteCase(BaseModel):
    suite = models.ForeignKey(TestSuite, on_delete=models.CASCADE, related_name="suite_cases")
    test_case = models.ForeignKey(TestCase, on_delete=models.CASCADE, related_name="suite_cases")
    seq = models.PositiveSmallIntegerField(default=1)

    class Meta(BaseModel.Meta):
        ordering = ("suite", "seq")
        constraints = [
            models.UniqueConstraint(fields=["suite", "test_case"], name="uniq_testsuite_case"),
        ]

    def __str__(self) -> str:
        return f"{self.suite.name}/{self.test_case.key}"


class TestRunTrigger(models.TextChoices):
    MANUAL = "manual", "Manual"
    CI = "ci", "CI"
    AI = "ai", "Triggered by AI"


class TestRunStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    PASSED = "passed", "Passed"
    FAILED = "failed", "Failed"
    ABORTED = "aborted", "Aborted"


class TestRun(BaseModel, ScopedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="test_runs")
    suite = models.ForeignKey(
        TestSuite, null=True, blank=True, on_delete=models.SET_NULL, related_name="runs"
    )
    environment = models.CharField(max_length=100, blank=True, default="")
    trigger = models.CharField(
        max_length=16, choices=TestRunTrigger.choices, default=TestRunTrigger.MANUAL
    )
    commit = models.ForeignKey(
        "codebase.Commit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="test_runs",
    )
    status = models.CharField(
        max_length=16, choices=TestRunStatus.choices, default=TestRunStatus.QUEUED
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    total = models.PositiveIntegerField(default=0)
    passed = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["project", "status"], name="idx_testrun_project_status"),
        ]

    def __str__(self) -> str:
        return f"run {self.pk} ({self.status})"

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[TestRun]:
        return cls.objects.filter(project__in=Project.objects.accessible_to(user))


class TestResultStatus(models.TextChoices):
    PASSED = "passed", "Passed"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"
    ERROR = "error", "Errored"


class TestResult(BaseModel):
    run = models.ForeignKey(TestRun, on_delete=models.CASCADE, related_name="results")
    test_case = models.ForeignKey(
        TestCase, null=True, blank=True, on_delete=models.SET_NULL, related_name="results"
    )
    #: The key from the report. Kept even when no matching TestCase exists, so a
    #: failing test can be reported before its case has been imported.
    case_key = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, choices=TestResultStatus.choices)
    duration_ms = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True, default="")
    stack_trace = models.TextField(blank=True, default="")
    attachments = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ("-created_at",)
        # The risk engine aggregates failure rates by result status.
        indexes = [
            models.Index(fields=["status"], name="idx_testresult_status"),
        ]

    def __str__(self) -> str:
        return f"{self.case_key or self.test_case_id} {self.status}"

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[TestResult]:
        return cls.objects.filter(run__project__in=Project.objects.accessible_to(user))


class CoverageSnapshot(BaseModel, ScopedModel):
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="coverage_snapshots"
    )
    commit = models.ForeignKey(
        "codebase.Commit",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="coverage_snapshots",
    )
    module = models.ForeignKey(
        "codebase.Module",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="coverage_snapshots",
    )
    line_rate = models.FloatField(default=0.0)
    branch_rate = models.FloatField(default=0.0)
    source = models.CharField(max_length=100, blank=True, default="")
    captured_at = models.DateTimeField()

    class Meta(BaseModel.Meta):
        ordering = ("-captured_at",)
        # The risk engine reads the latest snapshot per module.
        indexes = [
            models.Index(fields=["module", "-captured_at"], name="idx_coverage_module_captured"),
        ]

    def __str__(self) -> str:
        return f"coverage {self.line_rate:.2f}"

    @classmethod
    def scoped_for(cls, user: Any) -> models.QuerySet[CoverageSnapshot]:
        return cls.objects.filter(project__in=Project.objects.accessible_to(user))


class TestCaseModuleLink(EntityLink):
    """Which module a test case covers."""

    test_case = models.ForeignKey(TestCase, on_delete=models.CASCADE, related_name="module_links")
    module = models.ForeignKey(
        "codebase.Module", on_delete=models.CASCADE, related_name="test_case_links"
    )

    class Meta(EntityLink.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["test_case", "module"], name="uniq_testcase_module_link"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.test_case.key} -> {self.module.name}"


class TestCaseCommitLink(EntityLink):
    """A commit this test case has verified.

    This is what makes regression selection evidence-based rather than a guess:
    a test that exercised the same area before is a stronger candidate than one
    that merely sits in the same module.
    """

    test_case = models.ForeignKey(TestCase, on_delete=models.CASCADE, related_name="commit_links")
    commit = models.ForeignKey(
        "codebase.Commit", on_delete=models.CASCADE, related_name="test_case_links"
    )

    class Meta(EntityLink.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["test_case", "commit"], name="uniq_testcase_commit_link"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.test_case.key} @ {self.commit.short_sha}"


class TestCaseRequirementLink(EntityLink):
    test_case = models.ForeignKey(
        TestCase, on_delete=models.CASCADE, related_name="requirement_links"
    )
    requirement = models.ForeignKey(
        "requirements.Requirement", on_delete=models.CASCADE, related_name="test_case_links"
    )

    class Meta(EntityLink.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["test_case", "requirement"], name="uniq_testcase_requirement_link"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.test_case.key} -> {self.requirement.external_key}"
