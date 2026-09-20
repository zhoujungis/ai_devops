"""Read tools over test cases and runs."""

from __future__ import annotations

from pydantic import BaseModel, Field

from apps.ai.tools.base import Citation, Tool, ToolContext, ToolResult
from apps.testing.models import TestCase, TestRun, TestRunStatus

DEFAULT_LIMIT = 25


class SearchTestCaseArgs(BaseModel):
    query: str | None = Field(default=None, description="Text to look for in the title.")
    module_path_prefix: str | None = Field(
        default=None, description="Only cases that cover this module."
    )
    requirement_key: str | None = Field(
        default=None, description="Only cases linked to this requirement key, e.g. PAY-18."
    )
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=100)


class SearchTestCaseTool(Tool[SearchTestCaseArgs]):
    name = "search_test_case"
    description = (
        "Find test cases by title text, covered module or requirement. This is how a "
        "regression set is assembled: only recommend cases this tool returned."
    )
    args_model = SearchTestCaseArgs

    def run(self, context: ToolContext, args: SearchTestCaseArgs) -> ToolResult:
        queryset = TestCase.objects.filter(project=context.project).exclude(status="deprecated")
        if args.query:
            queryset = queryset.filter(title__icontains=args.query)
        if args.module_path_prefix:
            queryset = queryset.filter(
                module_links__module__path_prefix=args.module_path_prefix
            ).distinct()
        if args.requirement_key:
            queryset = queryset.filter(
                requirement_links__requirement__external_key__iexact=args.requirement_key
            ).distinct()

        test_cases = list(queryset.order_by("key")[: args.limit])
        return ToolResult(
            data=[
                {
                    "key": test_case.key,
                    "title": test_case.title,
                    "priority": test_case.priority,
                    "automation": test_case.automation,
                    "expected": test_case.expected[:300],
                }
                for test_case in test_cases
            ],
            citations=tuple(
                Citation(kind="test_case", ref_id=str(test_case.pk), label=test_case.key)
                for test_case in test_cases
            ),
        )


class ListTestRunsArgs(BaseModel):
    status: str | None = Field(default=None, description="Filter by run status.")
    limit: int = Field(default=10, ge=1, le=50)


class ListTestRunsTool(Tool[ListTestRunsArgs]):
    name = "list_test_runs"
    description = "Recent test runs for the project, newest first."
    args_model = ListTestRunsArgs

    def run(self, context: ToolContext, args: ListTestRunsArgs) -> ToolResult:
        queryset = TestRun.objects.filter(project=context.project)
        if args.status:
            queryset = queryset.filter(status=args.status)
        runs = list(queryset.order_by("-created_at")[: args.limit])
        return ToolResult(
            data=[
                {
                    "id": str(run.pk),
                    "status": run.status,
                    "environment": run.environment,
                    "total": run.total,
                    "passed": run.passed,
                    "failed": run.failed,
                    "created_at": run.created_at.isoformat(),
                }
                for run in runs
            ]
        )


class ListFailingTestsArgs(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


class ListFailingTestsTool(Tool[ListFailingTestsArgs]):
    name = "list_failing_tests"
    description = "Failures from the most recent failing test run, with error messages."
    args_model = ListFailingTestsArgs

    def run(self, context: ToolContext, args: ListFailingTestsArgs) -> ToolResult:
        run = (
            TestRun.objects.filter(project=context.project, status=TestRunStatus.FAILED)
            .order_by("-created_at")
            .first()
        )
        if run is None:
            return ToolResult(data={"run_id": None, "failures": []})

        failures = run.results.exclude(status="passed")[: args.limit]
        return ToolResult(
            data={
                "run_id": str(run.pk),
                "failures": [
                    {
                        "case_key": (
                            result.case_key or (result.test_case.key if result.test_case else "")
                        ),
                        "status": result.status,
                        "error_message": result.error_message[:500],
                    }
                    for result in failures
                ],
            }
        )
