"""The tool layer: what the model can reach, and what it cannot.

Two properties matter more than the individual tools: only read-only tools are ever
shown to the model, and every tool refuses rows from another project. Both are
asserted here rather than left to code review.
"""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import BaseModel

from apps.accounts.models import Organization, Project
from apps.accounts.tests.factories import (
    MembershipFactory,
    OrganizationFactory,
    ProjectFactory,
    UserFactory,
)
from apps.ai.tools.base import (
    Tool,
    ToolArgumentsError,
    ToolContext,
    ToolResult,
    ToolScopeError,
)
from apps.ai.tools.read.bugs import SearchBugArgs, SearchBugTool
from apps.ai.tools.read.codebase import (
    MAX_DIFF_BYTES,
    GetCommitArgs,
    GetCommitTool,
    GetDiffArgs,
    GetDiffTool,
    ListModulesArgs,
    ListModulesTool,
    SearchCommitArgs,
    SearchCommitTool,
)
from apps.ai.tools.read.correlation import ExplainCommitArgs, ExplainCommitTool
from apps.ai.tools.read.testing import ListFailingTestsArgs, ListFailingTestsTool
from apps.ai.tools.registry import ToolRegistry, load_default_tools, registry
from apps.bugs.models import Bug
from apps.codebase.ingest import ingest_commit
from apps.codebase.models import Commit, Module, ModuleKind
from apps.core.models import LinkSource
from apps.integrations.tests.factories import build_repository
from apps.integrations.tests.fakes import make_remote_commit, make_remote_file
from apps.requirements.models import Requirement
from apps.testing.models import (
    TestCase,
    TestCaseModuleLink,
    TestResult,
    TestRun,
    TestRunStatus,
)

pytestmark = pytest.mark.django_db


class _NoArgs(BaseModel):
    pass


class _WriteTool(Tool[_NoArgs]):
    """Stands in for a state-changing tool. Must never reach the model."""

    name = "create_test_case"
    description = "not exposed to the model"
    args_model = _NoArgs
    read_only = False

    def run(self, context: ToolContext, args: _NoArgs) -> ToolResult:
        raise AssertionError("write tools are only invoked after user confirmation")


def _scene(sha: str = "abc123") -> tuple[Project, Commit]:
    """A project with a synced commit.

    ``sha`` is a parameter because a commit sha is only unique *within* a
    repository: two projects can legitimately hold the same one, which is exactly
    why the tools scope by project rather than by sha alone.
    """
    org = cast(Organization, OrganizationFactory())
    MembershipFactory(org=org, user=UserFactory(), role="admin")
    project = cast(Project, ProjectFactory(org=org))
    repository = build_repository(project)
    commit = ingest_commit(
        repository,
        make_remote_commit(
            sha,
            minutes_ago=5,
            message="PAY-18 retry payment",
            files=[make_remote_file("src/payment/PaymentService.java")],
        ),
    )
    return project, commit


def _context(project: Project) -> ToolContext:
    return ToolContext(project=project)


# ---------------------------------------------------------------------------
# registry guarantees
# ---------------------------------------------------------------------------
def test_only_read_only_tools_are_exposed_to_the_model() -> None:
    custom = ToolRegistry()
    custom.register(GetCommitTool())
    custom.register(_WriteTool())

    exposed = {spec["function"]["name"] for spec in custom.model_specs()}

    assert exposed == {"get_commit"}
    assert "create_test_case" not in exposed
    # It is still resolvable, so a confirmed action can call it.
    assert custom.get("create_test_case").read_only is False


def test_a_duplicate_tool_name_is_a_registration_error() -> None:
    custom = ToolRegistry()
    custom.register(GetCommitTool())

    with pytest.raises(Exception, match="already registered"):
        custom.register(GetCommitTool())


# ---------------------------------------------------------------------------
# a tool must do what its description tells the model it does
# ---------------------------------------------------------------------------
def test_search_bug_matches_the_description_and_the_error_type() -> None:
    """Its description promises title, description and error type; the model trusts it."""
    project, _commit = _scene()
    Bug.objects.create(
        project=project, key="BUG-1", title="Unrelated", description="a cold upstream 504"
    )
    Bug.objects.create(project=project, key="BUG-2", title="Unrelated", error_type="TimeoutError")

    by_description = SearchBugTool().run(_context(project), SearchBugArgs(query="504"))
    by_error_type = SearchBugTool().run(_context(project), SearchBugArgs(query="TimeoutError"))

    assert [row["key"] for row in by_description.data] == ["BUG-1"]
    assert [row["key"] for row in by_error_type.data] == ["BUG-2"]


def test_list_modules_searches_the_name_as_well_as_the_path() -> None:
    project, _commit = _scene()
    Module.objects.create(
        project=project,
        path_prefix="src/zeta/",
        name="Billing Core",
        kind=ModuleKind.SERVICE,
        language="python",
    )

    by_name = ListModulesTool().run(_context(project), ListModulesArgs(query="Billing"))
    by_path = ListModulesTool().run(_context(project), ListModulesArgs(query="zeta"))

    assert [row["name"] for row in by_name.data] == ["Billing Core"]
    assert [row["path_prefix"] for row in by_path.data] == ["src/zeta/"]


def test_list_failing_tests_reports_failures_not_skips() -> None:
    """A skipped test never ran, so it is not evidence of a problem."""
    project, _commit = _scene()
    run = TestRun.objects.create(project=project, status=TestRunStatus.FAILED)
    TestResult.objects.create(run=run, case_key="T-1", status="failed", error_message="boom")
    TestResult.objects.create(run=run, case_key="T-2", status="skipped")
    TestResult.objects.create(run=run, case_key="T-3", status="passed")

    result = ListFailingTestsTool().run(_context(project), ListFailingTestsArgs())

    assert [row["case_key"] for row in result.data["failures"]] == ["T-1"]


def test_a_diff_is_bounded_in_bytes_not_characters() -> None:
    project, commit = _scene()
    changed = commit.files.get()
    changed.patch = "修" * 20_000  # 20k characters, 60 KB of UTF-8
    changed.save(update_fields=["patch"])

    result = GetDiffTool().run(_context(project), GetDiffArgs(sha=commit.sha))

    assert result.data["truncated"] is True
    assert len(result.data["files"][0]["patch"].encode("utf-8")) <= MAX_DIFF_BYTES


def test_an_unknown_tool_lists_what_exists() -> None:
    custom = ToolRegistry()
    custom.register(GetCommitTool())

    with pytest.raises(Exception, match="No tool named"):
        custom.get("nope")


def test_the_default_tool_set_loads_once_and_covers_the_chain_entry_point() -> None:
    load_default_tools()
    names_before = registry.names()
    load_default_tools()

    assert registry.names() == names_before
    assert "explain_commit" in names_before
    assert all(registry.get(name).read_only for name in names_before)


def test_every_default_tool_declares_a_description_and_a_schema() -> None:
    toolset = load_default_tools()

    for name in toolset.names():
        tool = toolset.get(name)
        assert tool.description, f"{name} has no description for the model to read"
        assert tool.args_model is not None


# ---------------------------------------------------------------------------
# scoping
# ---------------------------------------------------------------------------
def test_a_tool_refuses_a_commit_from_another_project() -> None:
    _mine, other_commit = _scene("other-sha")
    project, _commit = _scene("my-sha")

    with pytest.raises(ToolScopeError, match="No commit"):
        GetCommitTool().run(_context(project), GetCommitArgs(sha=other_commit.sha))


def test_search_never_returns_another_projects_rows() -> None:
    _other, other_commit = _scene("other-sha")
    project, own_commit = _scene("my-sha")

    result = SearchCommitTool().run(_context(project), SearchCommitArgs(query=""))

    shas = {row["sha"] for row in result.data}
    assert shas == {own_commit.sha}
    assert other_commit.sha not in shas


def test_a_malformed_identifier_is_an_argument_error_not_a_crash() -> None:
    project, _commit = _scene()

    with pytest.raises(ToolArgumentsError, match="not a valid identifier"):
        GetCommitTool().run(_context(project), GetCommitArgs(commit_id="not-a-uuid"))


def test_execute_validates_raw_model_arguments() -> None:
    project, _commit = _scene()

    with pytest.raises(ToolArgumentsError):
        GetCommitTool().execute(_context(project), {"unexpected": 1})


# ---------------------------------------------------------------------------
# behaviour
# ---------------------------------------------------------------------------
def test_explain_commit_returns_the_chain_and_its_citations() -> None:
    project, commit = _scene()
    requirement = Requirement.objects.create(project=project, external_key="PAY-18", title="Retry")
    commit.requirement = requirement
    commit.save(update_fields=["requirement"])

    module = Module.objects.get(project=project, path_prefix="src/payment")
    test_case = TestCase.objects.create(project=project, key="TC-001", title="Payment")
    TestCaseModuleLink.objects.create(test_case=test_case, module=module, source=LinkSource.MANUAL)

    result = ExplainCommitTool().run(_context(project), ExplainCommitArgs(sha=commit.sha))

    assert result.data["commit"]["sha"] == "abc123"
    assert [row["path_prefix"] for row in result.data["modules"]] == ["src/payment"]
    assert [row["key"] for row in result.data["regression_candidates"]] == ["TC-001"]
    assert result.data["requirement"]["external_key"] == "PAY-18"
    assert result.data["data_gaps"]

    kinds = {citation.kind for citation in result.citations}
    assert {"commit", "test_case", "requirement"} <= kinds


def test_explain_commit_refuses_another_projects_commit() -> None:
    _other, other_commit = _scene("other-sha")
    project, _commit = _scene("my-sha")

    with pytest.raises(ToolScopeError):
        ExplainCommitTool().run(_context(project), ExplainCommitArgs(sha=other_commit.sha))
