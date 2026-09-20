"""Git provider abstraction.

Everything provider-specific lives behind :class:`GitProvider`, so a second
provider (GitLab) is added by implementing this protocol rather than by touching
the sync or ingestion code.

The data-transfer objects mirror what the GitHub REST API actually returns, and
they are deliberately honest about its limits: listing commits does **not** return
changed files, so listing yields :class:`RemoteCommitRef` and the full commit is
fetched separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


class GitProviderError(RuntimeError):
    """Base class for git provider failures."""


class GitAuthenticationError(GitProviderError):
    """Credentials were rejected by the provider."""


class GitNotFoundError(GitProviderError):
    """The requested repository, branch or commit does not exist."""


class GitRateLimitError(GitProviderError):
    """The provider asked us to slow down; retry later."""


@dataclass(frozen=True)
class RemoteRepository:
    external_id: str
    full_name: str
    default_branch: str
    is_private: bool


@dataclass(frozen=True)
class RemoteBranch:
    name: str
    sha: str
    is_default: bool = False
    protected: bool = False


@dataclass(frozen=True)
class RemoteCommitRef:
    """A commit as returned by the list endpoint: identity and date only."""

    sha: str
    committed_at: datetime


@dataclass(frozen=True)
class RemoteCommitFile:
    path: str
    change_type: str
    additions: int = 0
    deletions: int = 0
    old_path: str | None = None
    patch: str | None = None
    # False when the provider returned no textual diff. The GitHub commit endpoint
    # omits ``patch`` for binary files *and* for diffs above its size limit, so this
    # is "no diff available", not "binary".
    has_patch: bool = False


@dataclass(frozen=True)
class RemoteCommit:
    sha: str
    message: str
    committed_at: datetime
    author_name: str = ""
    author_email: str = ""
    committer_email: str = ""
    parent_shas: list[str] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    files: list[RemoteCommitFile] = field(default_factory=list)


@runtime_checkable
class GitProvider(Protocol):
    """What the sync layer needs from any git host."""

    #: Remaining API budget reported by the host, when it reports one at all.
    rate_limit_remaining: int | None

    def close(self) -> None:
        """Release the underlying HTTP resources."""

    def verify(self) -> bool:
        """Return True when the stored credentials work."""

    def get_repository(self, full_name: str) -> RemoteRepository:
        """Repository metadata, used to resolve the default branch."""

    def list_branches(
        self, full_name: str, *, per_page: int = 100, page: int = 1
    ) -> list[RemoteBranch]:
        """Branches, newest page first."""

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
        """Commit identities on ``branch``, newest first.

        ``since`` and ``until`` bound the commit date. Walking *backwards* through
        history requires ``until``: ``since`` alone can only ever move forwards,
        which is why a truncated backfill cannot resume with it.
        """

    def get_commit(self, full_name: str, sha: str) -> RemoteCommit:
        """A single commit including its changed files."""
