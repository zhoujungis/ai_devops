"""Writing remote git data into local rows."""

from __future__ import annotations

from typing import Any

from apps.integrations.git.factory import provider_for
from apps.integrations.models import GitConnection, Repository


def connect_repository(
    *,
    project: Any,
    connection: GitConnection,
    full_name: str,
    sync_window_days: int | None = None,
    module_depth: int | None = None,
    module_overrides: dict[str, str] | None = None,
) -> Repository:
    """Register a repository, resolving its metadata from the provider.

    Idempotent on ``(project, external_id)``: connecting the same repository twice
    refreshes its metadata instead of creating a duplicate.
    """
    provider = provider_for(connection)
    try:
        remote = provider.get_repository(full_name)
    finally:
        provider.close()

    defaults: dict[str, Any] = {
        "connection": connection,
        "provider": connection.provider,
        "full_name": remote.full_name,
        "default_branch": remote.default_branch,
        "is_private": remote.is_private,
    }
    if sync_window_days is not None:
        defaults["sync_window_days"] = sync_window_days
    if module_depth is not None:
        defaults["module_depth"] = module_depth
    if module_overrides is not None:
        defaults["module_overrides"] = module_overrides

    repository, _ = Repository.objects.update_or_create(
        project=project,
        external_id=remote.external_id,
        defaults=defaults,
    )
    return repository
