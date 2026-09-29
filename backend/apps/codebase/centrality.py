"""How central each module is to its project.

Centrality is a property of the codebase, not of one commit: it is the share of the
project's commits that touched a module, normalised so the busiest module scores 1.0.
That is the scale :mod:`apps.risk.engine` expects — it saturates at 1.0 — and computing
it means the `module_centrality` signal cannot drift from the code the way a
hand-entered number would.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Count

from apps.codebase.models import CommitModuleImpact, Module


def recompute_centrality(project: Any) -> int:
    """Set ``centrality_score`` on every module of ``project``. Returns rows updated.

    One aggregate plus one bulk update regardless of how many modules there are, so it
    is cheap enough to run at the end of every sync.
    """
    touches = dict(
        CommitModuleImpact.objects.filter(module__project=project)
        .values_list("module_id")
        .annotate(total=Count("commit_id", distinct=True))
    )
    modules = list(Module.objects.filter(project=project))
    busiest = max(touches.values(), default=0)

    for module in modules:
        module.centrality_score = round(touches.get(module.pk, 0) / busiest, 4) if busiest else 0.0

    if modules:
        Module.objects.bulk_update(modules, ["centrality_score"])
    return len(modules)
