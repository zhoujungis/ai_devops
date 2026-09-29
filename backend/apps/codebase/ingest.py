"""Persisting remote git data into codebase rows."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import transaction

from apps.codebase.models import Branch, Commit, CommitFile, CommitModuleImpact, Module
from apps.codebase.modules import ModuleResolver, is_test_path, language_for_path
from apps.integrations.git.base import RemoteBranch, RemoteCommit
from apps.integrations.models import Repository


def resolver_for(repository: Repository) -> ModuleResolver:
    return ModuleResolver(
        depth=repository.module_depth,
        overrides=repository.module_overrides,
    )


def _prepare_patch(patch: str | None) -> tuple[str, bool]:
    """Bound the stored diff size. Returns ``(patch, truncated)``.

    Bound in *bytes*, not characters: the setting is a byte cap, and a CJK diff
    sliced by character can store three times the intended size. The slice is done
    on the encoded bytes and decoded back ignoring the split character at the cut.

    An absent diff is stored as an empty string; ``has_patch`` on the row is what
    distinguishes "the provider gave us nothing" from "the diff was empty".
    """
    if not patch:
        return "", False
    limit: int = settings.GIT_PATCH_MAX_BYTES
    encoded = patch.encode("utf-8")
    if len(encoded) <= limit:
        return patch, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


@transaction.atomic
def ingest_commit(repository: Repository, remote: RemoteCommit) -> Commit:
    """Create a commit with its files and module impacts, idempotently."""
    commit, created = Commit.objects.get_or_create(
        repository=repository,
        sha=remote.sha,
        defaults={
            "parent_shas": remote.parent_shas,
            "author_name": remote.author_name,
            "author_email": remote.author_email,
            "committer_email": remote.committer_email,
            "message": remote.message,
            "committed_at": remote.committed_at,
            "additions": remote.additions,
            "deletions": remote.deletions,
            "files_changed": len(remote.files),
        },
    )
    if not created:
        return commit

    CommitFile.objects.bulk_create(
        [
            CommitFile(
                commit=commit,
                path=item.path,
                old_path=item.old_path or "",
                change_type=item.change_type,
                additions=item.additions,
                deletions=item.deletions,
                patch=patch,
                has_patch=item.has_patch,
                truncated=truncated,
                language=language_for_path(item.path),
            )
            for item in remote.files
            for patch, truncated in [_prepare_patch(item.patch)]
        ]
    )

    rebuild_module_impacts(commit, resolver_for(repository))
    return commit


def rebuild_module_impacts(commit: Commit, resolver: ModuleResolver) -> list[CommitModuleImpact]:
    """Group the commit's files by module and materialise the impact edges."""
    files = list(commit.files.all())
    if not files:
        return []

    buckets: dict[str, dict[str, Any]] = {}
    for changed in files:
        match = resolver.resolve(changed.path)
        bucket = buckets.setdefault(
            match.path_prefix,
            {"match": match, "churn": 0, "files": 0, "test_files": 0},
        )
        bucket["churn"] += changed.additions + changed.deletions
        bucket["files"] += 1
        if is_test_path(changed.path):
            bucket["test_files"] += 1

    churn_total = sum(bucket["churn"] for bucket in buckets.values())
    file_total = len(files)
    # Churn is the better signal, but a pure rename carries none — fall back to
    # file counts so the weights still sum to ~1.
    weight_basis = churn_total if churn_total > 0 else file_total
    weight_key = "churn" if churn_total > 0 else "files"

    impacts = []
    for bucket in buckets.values():
        module, _ = Module.objects.get_or_create(
            project=commit.repository.project,
            path_prefix=bucket["match"].path_prefix,
            defaults={
                "name": bucket["match"].name,
                "kind": bucket["match"].kind,
                "language": bucket["match"].language,
            },
        )
        impacts.append(
            CommitModuleImpact(
                commit=commit,
                module=module,
                churn_lines=bucket["churn"],
                file_count=bucket["files"],
                weight=round(bucket[weight_key] / weight_basis, 4),
                is_test_change=bucket["test_files"] == bucket["files"],
            )
        )

    CommitModuleImpact.objects.filter(commit=commit).delete()
    return CommitModuleImpact.objects.bulk_create(impacts)


@transaction.atomic
def ingest_branch(repository: Repository, remote: RemoteBranch) -> Branch:
    branch, _ = Branch.objects.update_or_create(
        repository=repository,
        name=remote.name,
        defaults={
            "head_sha": remote.sha,
            "is_default": remote.is_default or remote.name == repository.default_branch,
            "is_protected": remote.protected,
        },
    )
    return branch
