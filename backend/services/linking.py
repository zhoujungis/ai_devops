"""Inferring links that nobody declared.

Kept separate from :mod:`services.correlation` because this module *writes*, and the
correlation service deliberately never does. Every link written here is recorded as
:class:`~apps.core.models.LinkSource.INFERRED`, never as manual, so a human
declaration can always be told apart from a guess.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from apps.codebase.models import Commit
from apps.integrations.models import Repository
from apps.requirements.models import Requirement

#: Requirement keys as teams actually write them in commit messages. Case
#: insensitive, matching the uppercased lookup below — a key typed as `pay-18`
#: still refers to `PAY-18`.
REQUIREMENT_KEY_PATTERN = re.compile(r"\b([A-Z]{2,10}-[0-9]{1,6})\b", re.IGNORECASE)

DEFAULT_LINK_LIMIT = 1000


def requirement_keys_for(project: Any) -> dict[str, Requirement]:
    """Every requirement key in the project, uppercased for matching."""
    return {
        requirement.external_key.upper(): requirement
        for requirement in Requirement.objects.filter(project=project)
    }


def find_requirement_key(text: str, keys: Mapping[str, Requirement]) -> Requirement | None:
    """The first requirement key mentioned in ``text``, if any.

    Only tokens that match a key the project actually has will link, which is what
    keeps incidental matches like ``UTF-8`` harmless.
    """
    for token in REQUIREMENT_KEY_PATTERN.findall(text or ""):
        found = keys.get(token.upper())
        if found is not None:
            return found
    return None


def link_commit_to_requirement(
    commit: Commit, *, keys: Mapping[str, Requirement] | None = None
) -> Requirement | None:
    """Point ``commit.requirement`` at a key found in its message."""
    resolved = keys if keys is not None else requirement_keys_for(commit.repository.project)
    requirement = find_requirement_key(commit.message, resolved)
    if requirement is None:
        return None
    if commit.requirement_id != requirement.pk:
        commit.requirement = requirement
        commit.save(update_fields=["requirement", "updated_at"])
    return requirement


def link_unlinked_commits(repository: Repository, *, limit: int = DEFAULT_LINK_LIMIT) -> int:
    """Batch version, for use right after a sync.

    Resolves the project's keys once instead of per commit, and returns how many
    commits gained a link.
    """
    keys = requirement_keys_for(repository.project)
    if not keys:
        return 0

    pending = list(Commit.objects.filter(repository=repository, requirement__isnull=True)[:limit])
    if not pending:
        return 0

    changed = []
    for commit in pending:
        requirement = find_requirement_key(commit.message, keys)
        if requirement is not None:
            commit.requirement = requirement
            changed.append(commit)

    if changed:
        Commit.objects.bulk_update(changed, ["requirement"])
    return len(changed)
