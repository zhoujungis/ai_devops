"""Read tools over the codebase: commits, diffs and modules."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db.models import Q, QuerySet
from pydantic import BaseModel, Field

from apps.ai.tools.base import (
    Citation,
    Tool,
    ToolArgumentsError,
    ToolContext,
    ToolResult,
    ToolScopeError,
)
from apps.codebase.models import Commit, Module

DEFAULT_LIMIT = 20
MAX_DIFF_BYTES = 20_000


def _by_identifier(queryset: QuerySet[Any], field: str, value: str) -> Any:
    """Look a row up by a value the model supplied, tolerating malformed ids."""
    try:
        return queryset.filter(**{field: value}).first()
    except (ValidationError, ValueError) as exc:
        raise ToolArgumentsError(f"{value!r} is not a valid identifier for {field}.") from exc


class GetCommitArgs(BaseModel):
    sha: str | None = Field(default=None, description="Commit sha, full or the 12-char prefix.")
    commit_id: str | None = Field(default=None, description="Internal commit id, if known.")


class GetCommitTool(Tool[GetCommitArgs]):
    name = "get_commit"
    description = (
        "Fetch one commit with its message, size and the modules it touched. "
        "Use this before reasoning about what a change affects."
    )
    args_model = GetCommitArgs

    def run(self, context: ToolContext, args: GetCommitArgs) -> ToolResult:
        queryset = Commit.objects.filter(repository__project=context.project).select_related(
            "requirement"
        )
        if args.commit_id:
            commit = _by_identifier(queryset, "pk", args.commit_id)
        elif args.sha:
            commit = (
                queryset.filter(sha=args.sha).first()
                or queryset.filter(sha__startswith=args.sha).first()
            )
        else:
            raise ToolArgumentsError("Provide either sha or commit_id.")

        if commit is None:
            raise ToolScopeError(
                f"No commit matching {args.sha or args.commit_id} in this project."
            )

        impacts = commit.module_impacts.select_related("module").order_by("-weight")
        return ToolResult(
            data={
                "sha": commit.sha,
                "short_sha": commit.short_sha,
                "message": commit.message.splitlines()[0] if commit.message else "",
                "author_name": commit.author_name,
                "committed_at": commit.committed_at.isoformat(),
                "additions": commit.additions,
                "deletions": commit.deletions,
                "files_changed": commit.files_changed,
                "requirement_key": (
                    commit.requirement.external_key if commit.requirement_id else None
                ),
                "modules": [
                    {
                        "path_prefix": impact.module.path_prefix,
                        "name": impact.module.name,
                        "weight": impact.weight,
                        "churn_lines": impact.churn_lines,
                        "is_test_change": impact.is_test_change,
                    }
                    for impact in impacts
                ],
            },
            citations=(Citation(kind="commit", ref_id=str(commit.pk), label=commit.short_sha),),
        )


class ListChangedFilesArgs(BaseModel):
    sha: str = Field(description="Commit sha to inspect.")


class ListChangedFilesTool(Tool[ListChangedFilesArgs]):
    name = "list_changed_files"
    description = "List the files a commit changed, with change type and line counts."
    args_model = ListChangedFilesArgs

    def run(self, context: ToolContext, args: ListChangedFilesArgs) -> ToolResult:
        commit = Commit.objects.filter(repository__project=context.project, sha=args.sha).first()
        if commit is None:
            raise ToolScopeError(f"No commit {args.sha} in this project.")

        files = commit.files.all()
        return ToolResult(
            data=[
                {
                    "path": changed.path,
                    "change_type": changed.change_type,
                    "additions": changed.additions,
                    "deletions": changed.deletions,
                    "language": changed.language,
                    "has_patch": changed.has_patch,
                }
                for changed in files
            ],
            citations=(Citation(kind="commit", ref_id=str(commit.pk), label=commit.short_sha),),
        )


class GetDiffArgs(BaseModel):
    sha: str = Field(description="Commit sha to inspect.")
    path: str | None = Field(
        default=None, description="Restrict to one changed file. Omit for all files."
    )


class GetDiffTool(Tool[GetDiffArgs]):
    name = "get_git_diff"
    description = (
        "Fetch the textual diff of a commit, optionally for one file. Large diffs are "
        "truncated; the result says so when it is."
    )
    args_model = GetDiffArgs

    def run(self, context: ToolContext, args: GetDiffArgs) -> ToolResult:
        commit = Commit.objects.filter(repository__project=context.project, sha=args.sha).first()
        if commit is None:
            raise ToolScopeError(f"No commit {args.sha} in this project.")

        changed_files = commit.files.all()
        if args.path:
            changed_files = changed_files.filter(path=args.path)
            if not changed_files.exists():
                raise ToolScopeError(f"Commit {args.sha} did not change {args.path!r}.")

        hunks: list[dict[str, Any]] = []
        total = 0
        truncated = False
        for changed in changed_files:
            stored = changed.patch or ""
            remaining = MAX_DIFF_BYTES - total
            encoded = stored.encode("utf-8")
            if len(encoded) > remaining:
                # Bounded in bytes, not characters: a CJK diff sliced by character would
                # let the prompt blow past the budget this constant advertises.
                stored = encoded[: max(remaining, 0)].decode("utf-8", errors="ignore")
                truncated = True
            total += len(stored.encode("utf-8"))
            hunks.append(
                {"path": changed.path, "patch": stored, "stored_truncated": changed.truncated}
            )
            if total >= MAX_DIFF_BYTES:
                break

        return ToolResult(
            data={"sha": args.sha, "truncated": truncated, "files": hunks},
            citations=(Citation(kind="commit", ref_id=str(commit.pk), label=commit.short_sha),),
        )


class SearchCommitArgs(BaseModel):
    query: str | None = Field(default=None, description="Text to look for in the message.")
    module_path_prefix: str | None = Field(
        default=None, description="Only commits that touched this module."
    )
    author_email: str | None = Field(default=None, description="Exact author email.")
    since: str | None = Field(default=None, description="ISO-8601 lower bound on commit date.")
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=100)


class SearchCommitTool(Tool[SearchCommitArgs]):
    name = "search_commit"
    description = "Find commits by message text, module, author or date range."
    args_model = SearchCommitArgs

    def run(self, context: ToolContext, args: SearchCommitArgs) -> ToolResult:
        queryset = Commit.objects.filter(repository__project=context.project)
        if args.query:
            queryset = queryset.filter(message__icontains=args.query)
        if args.module_path_prefix:
            queryset = queryset.filter(
                module_impacts__module__path_prefix=args.module_path_prefix
            ).distinct()
        if args.author_email:
            queryset = queryset.filter(author_email=args.author_email)
        if args.since:
            queryset = queryset.filter(committed_at__gte=args.since)

        commits = list(queryset.order_by("-committed_at")[: args.limit])
        return ToolResult(
            data=[
                {
                    "sha": commit.sha,
                    "short_sha": commit.short_sha,
                    "message": commit.message.splitlines()[0] if commit.message else "",
                    "committed_at": commit.committed_at.isoformat(),
                    "author_name": commit.author_name,
                }
                for commit in commits
            ],
            citations=tuple(
                Citation(kind="commit", ref_id=str(commit.pk), label=commit.short_sha)
                for commit in commits
            ),
        )


class ListModulesArgs(BaseModel):
    query: str | None = Field(default=None, description="Filter by name or path prefix.")


class ListModulesTool(Tool[ListModulesArgs]):
    name = "list_modules"
    description = "List the project's resolved modules and their path prefixes."
    args_model = ListModulesArgs

    def run(self, context: ToolContext, args: ListModulesArgs) -> ToolResult:
        modules = Module.objects.filter(project=context.project)
        if args.query:
            # Matches the tool's description: name *or* path prefix.
            modules = modules.filter(
                Q(name__icontains=args.query) | Q(path_prefix__icontains=args.query)
            )
        rows = list(modules.order_by("path_prefix")[:100])
        return ToolResult(
            data=[
                {
                    "path_prefix": module.path_prefix,
                    "name": module.name,
                    "kind": module.kind,
                    "language": module.language,
                }
                for module in rows
            ]
        )
