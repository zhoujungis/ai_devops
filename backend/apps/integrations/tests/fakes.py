"""An in-memory :class:`GitProvider` so sync logic can be tested deterministically.

It reproduces the one behaviour of the real API that the sync design depends on:
commits are listed newest-first, and ``since`` filters by commit date.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from apps.integrations.git.base import (
    GitNotFoundError,
    RemoteBranch,
    RemoteCommit,
    RemoteCommitFile,
    RemoteCommitRef,
    RemoteRepository,
)

DEFAULT_FULL_NAME = "acme/payments"


def make_remote_commit(
    sha: str,
    *,
    minutes_ago: int,
    files: list[RemoteCommitFile] | None = None,
    message: str = "change",
    now: datetime | None = None,
) -> RemoteCommit:
    moment = (now or datetime.now(UTC)) - timedelta(minutes=minutes_ago)
    changed = files if files is not None else [make_remote_file("src/app/main.py")]
    return RemoteCommit(
        sha=sha,
        message=message,
        committed_at=moment,
        author_name="Ada Lovelace",
        author_email="ada@example.com",
        committer_email="ada@example.com",
        parent_shas=[],
        additions=sum(item.additions for item in changed),
        deletions=sum(item.deletions for item in changed),
        files=changed,
    )


def make_remote_file(
    path: str,
    *,
    additions: int = 3,
    deletions: int = 1,
    change_type: str = "modify",
    patch: str | None = "@@ -1,3 +1,3 @@\n-old\n+new",
) -> RemoteCommitFile:
    return RemoteCommitFile(
        path=path,
        change_type=change_type,
        additions=additions,
        deletions=deletions,
        patch=patch,
        has_patch=patch is not None,
    )


class FakeGitProvider:
    """Serves a fixed commit list, honouring ``since`` and pagination."""

    def __init__(
        self,
        *,
        commits: list[RemoteCommit] | None = None,
        branches: list[RemoteBranch] | None = None,
        repository: RemoteRepository | None = None,
        full_name: str = DEFAULT_FULL_NAME,
    ) -> None:
        # Newest first, exactly like the real endpoint.
        self.commits: list[RemoteCommit] = sorted(
            commits or [], key=lambda item: item.committed_at, reverse=True
        )
        self.branches = branches or [RemoteBranch(name="main", sha="head", is_default=True)]
        self.full_name = full_name
        self.repository = repository or RemoteRepository(
            external_id="42",
            full_name=full_name,
            default_branch="main",
            is_private=False,
        )
        self.rate_limit_remaining: int | None = 4999
        self.closed = False
        self.ref_pages_requested = 0

    def close(self) -> None:
        self.closed = True

    def verify(self) -> bool:
        return True

    def get_repository(self, full_name: str) -> RemoteRepository:
        return self.repository

    def list_branches(
        self, full_name: str, *, per_page: int = 100, page: int = 1
    ) -> list[RemoteBranch]:
        return self.branches if page == 1 else []

    def list_commit_refs(
        self,
        full_name: str,
        *,
        branch: str,
        since: datetime | None = None,
        until: datetime | None = None,
        per_page: int = 100,
        page: int = 1,
    ) -> list[RemoteCommitRef]:
        self.ref_pages_requested += 1
        refs = [
            RemoteCommitRef(sha=item.sha, committed_at=item.committed_at) for item in self.commits
        ]
        if since is not None:
            refs = [ref for ref in refs if ref.committed_at >= since]
        if until is not None:
            refs = [ref for ref in refs if ref.committed_at <= until]
        start = (page - 1) * per_page
        return refs[start : start + per_page]

    def get_commit(self, full_name: str, sha: str) -> RemoteCommit:
        for commit in self.commits:
            if commit.sha == sha:
                return commit
        raise GitNotFoundError(sha)


def failing_provider(exc: Exception) -> Any:
    """A provider whose listing always raises, for failure-path tests."""

    class _Failing(FakeGitProvider):
        def list_commit_refs(self, *args: Any, **kwargs: Any) -> list[RemoteCommitRef]:
            raise exc

    return _Failing()
