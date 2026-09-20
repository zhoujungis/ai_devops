"""Object-level scoping.

Every model exposed through the API must declare how its queryset narrows for a
given user. :func:`scoped_queryset` is the only sanctioned entry point, so a view
that forgets a permission check fails closed instead of leaking another tenant's
rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.core.exceptions import ImproperlyConfigured
from django.db import models

if TYPE_CHECKING:
    from django.db.models import QuerySet


class ScopedModel(models.Model):
    """Contract for models that are reachable only through a tenant scope."""

    class Meta:
        abstract = True

    @classmethod
    def scoped_for(cls, user: Any) -> QuerySet[Any]:
        """Return the rows ``user`` is allowed to see."""
        raise NotImplementedError(
            f"{cls.__name__} must implement scoped_for() before it can be exposed via the API."
        )


def scoped_queryset(model: type[ScopedModel], user: Any) -> QuerySet[Any]:
    """Build the queryset for an API request, narrowed to ``user``'s scope.

    Raises ``ImproperlyConfigured`` for models that never declared a scope, so the
    mistake surfaces in tests rather than as a cross-tenant leak in production.
    """
    if not (isinstance(model, type) and issubclass(model, ScopedModel)):
        raise ImproperlyConfigured(
            f"{getattr(model, '__name__', model)!r} is not a ScopedModel: declare how it is "
            "scoped before exposing it through the API."
        )
    return model.scoped_for(user)
