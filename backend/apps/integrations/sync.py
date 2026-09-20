"""Repository synchronisation.

History moves in two directions and they cannot share one cursor:

* **catching up** only ever moves forward, bounded by ``since``;
* **backfilling** a window larger than one run's commit budget has to move
  *backwards*, and ``since`` cannot express that. It needs ``until``.

Using a single "we hold everything newer than X" boundary looks correct until a
backfill is truncated: the boundary then sits above the history that was never
fetched, and ``since`` can never reach below it again — the gap is lost silently.
So the cursor keeps both ends separate:

``backfill_before``
    Walk downwards to here next. ``None`` once the sync window floor is reached.
``newest_seen``
    Everything up to here has been considered on the forward pass.

Each pass ingests contiguously outwards from the region already covered (the
backfill newest-first, the forward pass oldest-first), so a run that stops early
resumes exactly where it stopped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.codebase.ingest import ingest_branch, ingest_commit
from apps.codebase.models import Commit
from apps.integrations.git.base import GitProvider, RemoteCommitRef
from apps.integrations.models import Repository, SyncStatus
from services.linking import link_unlinked_commits

PAGE_SIZE = 100
#: Re-read a little either side of a cursor so a commit sharing a timestamp with
#: it is never skipped. Duplicates are harmless: ingestion is idempotent.
_RESUME_OVERLAP = timedelta(minutes=5)
_ONE_SECOND = timedelta(seconds=1)


@dataclass
class SyncCursor:
    backfill_before: datetime | None
    newest_seen: datetime | None


@dataclass
class SyncResult:
    repository_id: str
    commits_seen: int = 0
    commits_created: int = 0
    branches_updated: int = 0
    pages_fetched: int = 0
    requirement_links: int = 0
    truncated: bool = False
    rate_limit_remaining: int | None = None
    backfill_before: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository_id": self.repository_id,
            "commits_seen": self.commits_seen,
            "commits_created": self.commits_created,
            "branches_updated": self.branches_updated,
            "pages_fetched": self.pages_fetched,
            "requirement_links": self.requirement_links,
            "truncated": self.truncated,
            "rate_limit_remaining": self.rate_limit_remaining,
            "backfill_before": self.backfill_before,
        }


def sync_repository(
    repository: Repository,
    *,
    provider: GitProvider,
    limit: int | None = None,
    now: datetime | None = None,
) -> SyncResult:
    """Bring one repository up to date. Idempotent, so a retry is always safe."""
    budget = settings.GIT_SYNC_MAX_COMMITS if limit is None else limit
    moment = now or timezone.now()
    result = SyncResult(repository_id=str(repository.pk))

    repository.sync_status = SyncStatus.RUNNING
    repository.sync_error = ""
    repository.save(update_fields=["sync_status", "sync_error", "updated_at"])

    try:
        floor = moment - timedelta(days=repository.sync_window_days)
        cursor = _read_cursor(repository, moment=moment)
        remaining = budget

        if cursor.backfill_before is not None:
            cursor.backfill_before, spent = _backfill(
                repository,
                provider,
                floor=floor,
                from_moment=cursor.backfill_before,
                budget=remaining,
                result=result,
            )
            remaining -= spent

        if remaining > 0:
            cursor.newest_seen = _sync_new_commits(
                repository,
                provider,
                since=cursor.newest_seen or (moment - _RESUME_OVERLAP),
                budget=remaining,
                moment=moment,
                result=result,
            )

        result.truncated = budget > 0 and remaining <= 0
        result.branches_updated = _refresh_branches(repository, provider)
        result.rate_limit_remaining = provider.rate_limit_remaining

        # Infer requirement links for whatever gained a commit this run. Batched,
        # so the project's requirement keys are resolved once rather than per commit.
        result.requirement_links = link_unlinked_commits(repository)

        repository.sync_cursor = _dump_cursor(cursor)
        repository.sync_status = SyncStatus.SUCCEEDED
        repository.last_synced_at = moment
        repository.sync_error = ""
        repository.save(
            update_fields=[
                "sync_cursor",
                "sync_status",
                "last_synced_at",
                "sync_error",
                "updated_at",
            ]
        )
        result.backfill_before = (
            cursor.backfill_before.isoformat() if cursor.backfill_before else None
        )
    except Exception as exc:
        repository.sync_status = SyncStatus.FAILED
        repository.sync_error = f"{type(exc).__name__}: {exc}"[:2000]
        repository.save(update_fields=["sync_status", "sync_error", "updated_at"])
        raise

    return result


# ---------------------------------------------------------------------------
# cursor
# ---------------------------------------------------------------------------
def _read_cursor(repository: Repository, *, moment: datetime) -> SyncCursor:
    raw: dict[str, Any] = repository.sync_cursor or {}
    if not raw:
        # First ever run: walk backwards from now, nothing seen forwards yet.
        return SyncCursor(
            backfill_before=moment,
            newest_seen=moment - _RESUME_OVERLAP,
        )
    return SyncCursor(
        backfill_before=_parse_moment(raw.get("backfill_before")),
        newest_seen=_parse_moment(raw.get("newest_seen")),
    )


def _dump_cursor(cursor: SyncCursor) -> dict[str, str | None]:
    return {
        "backfill_before": (
            cursor.backfill_before.isoformat() if cursor.backfill_before is not None else None
        ),
        "newest_seen": (cursor.newest_seen.isoformat() if cursor.newest_seen is not None else None),
    }


def _parse_moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# passes
# ---------------------------------------------------------------------------
def _backfill(
    repository: Repository,
    provider: GitProvider,
    *,
    floor: datetime,
    from_moment: datetime,
    budget: int,
    result: SyncResult,
) -> tuple[datetime | None, int]:
    """Walk backwards from ``from_moment``. Returns ``(resume_point, spent)``.

    ``resume_point`` is ``None`` once the window floor has been reached.
    """
    until = from_moment
    resume: datetime | None = until
    spent = 0
    page = 1

    while spent < budget:
        refs = provider.list_commit_refs(
            repository.full_name,
            branch=repository.default_branch,
            since=floor,
            until=until,
            per_page=PAGE_SIZE,
            page=page,
        )
        result.pages_fetched += 1
        if not refs:
            return None, spent

        result.commits_seen += len(refs)
        known = _known_shas(repository, refs)

        # Newest first: the region we already hold grows downwards contiguously.
        for ref in sorted(refs, key=lambda item: item.committed_at, reverse=True):
            if spent >= budget:
                return resume, spent
            if ref.sha in known:
                continue
            ingest_commit(repository, provider.get_commit(repository.full_name, ref.sha))
            spent += 1
            result.commits_created += 1
            resume = ref.committed_at - _ONE_SECOND

        if len(refs) < PAGE_SIZE:
            # Nothing older left inside the sync window.
            return None, spent
        if min(ref.committed_at for ref in refs) <= floor:
            return None, spent
        page += 1

    return resume, spent


def _sync_new_commits(
    repository: Repository,
    provider: GitProvider,
    *,
    since: datetime,
    budget: int,
    moment: datetime,
    result: SyncResult,
) -> datetime:
    """Walk forwards from ``since``. Returns the new ``newest_seen``."""
    newest = since
    spent = 0
    page = 1

    while spent < budget:
        refs = provider.list_commit_refs(
            repository.full_name,
            branch=repository.default_branch,
            since=since,
            per_page=PAGE_SIZE,
            page=page,
        )
        result.pages_fetched += 1
        if not refs:
            return max(newest, moment - _RESUME_OVERLAP)

        result.commits_seen += len(refs)
        known = _known_shas(repository, refs)

        # Oldest first: the region we already hold grows upwards contiguously.
        for ref in sorted(refs, key=lambda item: item.committed_at):
            if spent >= budget:
                return newest
            if ref.sha not in known:
                ingest_commit(repository, provider.get_commit(repository.full_name, ref.sha))
                spent += 1
                result.commits_created += 1
            newest = max(newest, ref.committed_at)

        if len(refs) < PAGE_SIZE:
            return max(newest, moment - _RESUME_OVERLAP)
        page += 1

    return newest


def _refresh_branches(repository: Repository, provider: GitProvider) -> int:
    """Refresh the first page of branches; a repository may have hundreds."""
    branches = provider.list_branches(repository.full_name, per_page=PAGE_SIZE, page=1)
    for branch in branches:
        ingest_branch(repository, branch)
    return len(branches)


def _known_shas(repository: Repository, refs: list[RemoteCommitRef]) -> set[str]:
    return set(
        Commit.objects.filter(repository=repository, sha__in=[ref.sha for ref in refs]).values_list(
            "sha", flat=True
        )
    )
