"""Ordering, with enum columns ordered by meaning rather than by spelling.

``?ordering=severity`` on a text column sorts ``critical`` before ``high`` before
``info`` — alphabetical, and not what anyone means by "worst first". A view declares
the order it means with ``ranked_ordering`` and this filter turns that into an
annotation, so the ordering the API advertises is the ordering it actually applies.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Case, IntegerField, Value, When
from rest_framework.filters import OrderingFilter


class RankedOrderingFilter(OrderingFilter):
    """An :class:`OrderingFilter` that understands ``ranked_ordering`` on the view.

    ``ranked_ordering`` maps a field name to ``{stored value: rank}``, where a higher
    rank sorts later. Fields not listed are ordered exactly as before, so a view only
    pays for this where alphabetical order would be wrong.
    """

    def filter_queryset(self, request: Any, queryset: Any, view: Any) -> Any:
        ordering = self.get_ordering(request, queryset, view)
        if not ordering:
            return queryset

        ranked: dict[str, dict[str, int]] = getattr(view, "ranked_ordering", {})
        annotations: dict[str, Any] = {}
        translated: list[str] = []

        for term in ordering:
            descending = term.startswith("-")
            field = term.lstrip("-")
            ranks = ranked.get(field)
            if ranks:
                # One alias per ranked field, so `?ordering=severity,-severity` cannot
                # build the same annotation twice.
                alias = f"rank_{field}"
                annotations[alias] = Case(
                    *[When(**{field: value}, then=Value(rank)) for value, rank in ranks.items()],
                    default=Value(0),
                    output_field=IntegerField(),
                )
                field = alias
            translated.append(f"-{field}" if descending else field)

        if annotations:
            queryset = queryset.annotate(**annotations)
        return queryset.order_by(*translated)
