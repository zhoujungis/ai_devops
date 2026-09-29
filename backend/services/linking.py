"""Inferring links that nobody declared.

Kept separate from :mod:`services.correlation` because this module *writes*, and the
correlation service deliberately never does. Every link written here is recorded as
:class:`~apps.core.models.LinkSource.INFERRED`, never as manual, so a human
declaration can always be told apart from a guess.

A commit message that names a requirement or a defect is the same kind of
human-supplied evidence in both cases, so both are read the same way — and the
commit's modules and requirement are carried across to the defect, because "this
defect lives in these modules" is what the risk engine's bug-density and
open-severity signals read.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, TypeVar

from apps.bugs.models import Bug, BugCommitLink, BugModuleLink, BugRequirementLink
from apps.codebase.models import Commit
from apps.core.models import LinkSource
from apps.integrations.models import Repository
from apps.requirements.models import Requirement

#: Human keys as teams actually write them in commit messages — `PAY-18`, `QAC-201`.
#: Case insensitive, matching the uppercased lookup below: a key typed as `pay-18`
#: still refers to `PAY-18`.
KEY_PATTERN = re.compile(r"\b([A-Z]{2,10}-[0-9]{1,6})\b", re.IGNORECASE)

DEFAULT_LINK_LIMIT = 1000

#: An inferred edge is real evidence but weaker than a declaration, so it is weighted
#: below 1.0 (the correlation engine uses the same figure for `LinkSource.INFERRED`).
INFERRED_CONFIDENCE = 0.75

TLinked = TypeVar("TLinked")


def requirement_keys_for(project: Any) -> dict[str, Requirement]:
    """Every requirement key in the project, uppercased for matching."""
    return {
        requirement.external_key.upper(): requirement
        for requirement in Requirement.objects.filter(project=project)
    }


def bug_keys_for(project: Any) -> dict[str, Bug]:
    """Every defect key in the project, uppercased for matching."""
    return {bug.key.upper(): bug for bug in Bug.objects.filter(project=project)}


def find_key(text: str, keys: Mapping[str, TLinked]) -> TLinked | None:
    """The first key mentioned in ``text``, if the project actually has it.

    Only tokens that match a key the project has will link, which is what keeps
    incidental matches like ``UTF-8`` harmless.
    """
    for token in KEY_PATTERN.findall(text or ""):
        found = keys.get(token.upper())
        if found is not None:
            return found
    return None


def link_commit_to_requirement(
    commit: Commit, *, keys: Mapping[str, Requirement] | None = None
) -> Requirement | None:
    """Point ``commit.requirement`` at a key found in its message."""
    resolved = keys if keys is not None else requirement_keys_for(commit.repository.project)
    requirement = find_key(commit.message, resolved)
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
        requirement = find_key(commit.message, keys)
        if requirement is not None:
            commit.requirement = requirement
            changed.append(commit)

    if changed:
        Commit.objects.bulk_update(changed, ["requirement"])
    return len(changed)


def link_bugs_in_commits(repository: Repository, *, limit: int = DEFAULT_LINK_LIMIT) -> int:
    """Attach commits to the defects their messages name, and carry the context over.

    Returns how many commits named a known defect. Idempotent: the links are created
    with ``ignore_conflicts`` so a re-sync adds only what is missing.
    """
    bugs = bug_keys_for(repository.project)
    if not bugs:
        return 0

    commit_links: list[BugCommitLink] = []
    module_links: list[BugModuleLink] = []
    requirement_links: list[BugRequirementLink] = []
    named = 0

    commits = (
        Commit.objects.filter(repository=repository)
        .select_related("requirement")
        .prefetch_related("module_impacts")
        .order_by("-committed_at")[:limit]
    )
    for commit in commits:
        bug = find_key(commit.message, bugs)
        if bug is None:
            continue
        named += 1
        commit_links.append(
            BugCommitLink(
                bug=bug,
                commit=commit,
                source=LinkSource.INFERRED,
                confidence=INFERRED_CONFIDENCE,
            )
        )
        module_links.extend(
            BugModuleLink(
                bug=bug,
                module_id=impact.module_id,
                source=LinkSource.INFERRED,
                confidence=INFERRED_CONFIDENCE,
            )
            for impact in commit.module_impacts.all()
        )
        if commit.requirement_id is not None:
            requirement_links.append(
                BugRequirementLink(
                    bug=bug,
                    requirement_id=commit.requirement_id,
                    source=LinkSource.INFERRED,
                    confidence=INFERRED_CONFIDENCE,
                )
            )

    if commit_links:
        BugCommitLink.objects.bulk_create(commit_links, ignore_conflicts=True)
    if module_links:
        BugModuleLink.objects.bulk_create(module_links, ignore_conflicts=True)
    if requirement_links:
        BugRequirementLink.objects.bulk_create(requirement_links, ignore_conflicts=True)
    return named
