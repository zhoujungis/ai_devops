"""The audit trail.

Organization-wide and admin-only. This is the terminal's answer to "who confirmed
this, and when" — the same question the Django admin answers, without leaving the
shell.
"""

from __future__ import annotations

from typing import Any

import typer

from ai_devops_cli import context, output


def list_audit(
    action: str | None = typer.Option(None, "--action", help="Filter by action code."),
    actor_type: str | None = typer.Option(None, "--actor-type", help="user|ai|system."),
    limit: int = typer.Option(50, "--limit", "-n", help="How many entries to show."),
) -> None:
    """Show recent state changes, newest first."""
    params: dict[str, Any] = {
        key: value for key, value in (("action", action), ("actor_type", actor_type)) if value
    }
    rows = context.client().results(f"{context.org_base()}/audit-logs", limit=limit, **params)

    if output.is_json():
        output.emit(rows)
        return
    output.table(
        "Audit log",
        ["WHEN", "ACTOR", "ACTION", "TARGET", "REQUEST"],
        [
            [
                output.timestamp(row["created_at"]),
                f"{row['actor_type']}:{row['actor_id'] or '-'}",
                row["action"],
                f"{row['target_type']}:{row['target_id'] or '-'}",
                row["request_id"] or "-",
            ]
            for row in rows
        ],
        empty="Nothing recorded yet.",
    )
