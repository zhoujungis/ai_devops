"""Install pgvector.

The extension is created with ``IF NOT EXISTS`` so this migration is a no-op when
an operator (or ``template1``) already provided it, which is the normal case in
managed PostgreSQL environments. Creating the extension itself requires
superuser rights because pgvector is not marked ``trusted`` upstream.
"""

from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS vector;",
            reverse_sql="DROP EXTENSION IF EXISTS vector;",
        ),
    ]
