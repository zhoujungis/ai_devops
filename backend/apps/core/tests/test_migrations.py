"""The pgvector extension must exist before the first ``vector`` column.

CI starts from an empty cluster, so the migration graph alone decides whether the very
first ``CREATE TABLE ... vector(1536)`` succeeds. Reading the ordering off the graph is
the only way to catch a missing edge without dropping a database — and a missing edge is
invisible locally, where the extension has been installed for a long time and every new
database inherits it from the same cluster.

It reads the graph rather than a database, so it needs no ``django_db``.
"""

from __future__ import annotations

from collections.abc import Sequence

from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations import AddField, AlterField, CreateModel
from django.db.migrations.operations.base import Operation
from pgvector.django import VectorField

EXTENSION = ("core", "0001_enable_pgvector")


def _vector_columns(operations: Sequence[Operation]) -> list[str]:
    """Every ``vector`` column a migration creates or adds."""
    columns: list[str] = []
    for operation in operations:
        if isinstance(operation, CreateModel):
            columns.extend(
                f"{operation.name}.{name}"
                for name, field in operation.fields
                if isinstance(field, VectorField)
            )
        elif isinstance(operation, AddField | AlterField) and isinstance(
            operation.field, VectorField
        ):
            columns.append(f"{operation.model_name}.{operation.name}")
    return columns


def test_every_vector_column_is_created_after_the_extension() -> None:
    loader = MigrationLoader(connection=None, ignore_no_migrations=True)
    graph = loader.graph
    assert EXTENSION in graph.nodes, f"{EXTENSION} is not a migration any more"

    offenders: list[str] = []
    for key, migration in loader.disk_migrations.items():
        columns = _vector_columns(migration.operations)
        if not columns:
            continue
        plan = graph.forwards_plan(key)
        if EXTENSION not in plan or plan.index(EXTENSION) > plan.index(key):
            offenders.append(f"{key[0]}.{key[1]} ({', '.join(columns)})")

    assert not offenders, (
        f"these migrations create a pgvector column before {EXTENSION[0]}.{EXTENSION[1]} "
        f"would have run: {', '.join(sorted(offenders))}"
    )
