"""The read-only trackers: requirements, tests, bugs and releases."""

from __future__ import annotations

from typing import Any

import typer

from ai_devops_cli import context, output


def _filters(**candidates: str | None) -> dict[str, Any]:
    return {key: value for key, value in candidates.items() if value}


def requirements(
    status: str | None = typer.Option(
        None, "--status", help="draft|approved|in_progress|implemented|verified."
    ),
    priority: str | None = typer.Option(None, "--priority", help="p0|p1|p2|p3."),
    limit: int = typer.Option(50, "--limit", "-n", help="How many to list."),
) -> None:
    """List requirements in the selected project."""
    rows = context.client().results(
        f"{context.project_base()}/requirements",
        limit=limit,
        **_filters(status=status, priority=priority),
    )
    if output.is_json():
        output.emit(rows)
        return
    output.table(
        "Requirements",
        ["KEY", "TITLE", "STATUS", "PRIORITY", "SPRINT"],
        [
            [r["external_key"], r["title"], r["status"], r["priority"], r.get("sprint", "")]
            for r in rows
        ],
        empty="No requirements.",
    )


def test_cases(
    status: str | None = typer.Option(None, "--status", help="draft|active|deprecated."),
    priority: str | None = typer.Option(None, "--priority", help="p0|p1|p2|p3."),
    limit: int = typer.Option(50, "--limit", "-n", help="How many to list."),
) -> None:
    """List test cases in the selected project."""
    rows = context.client().results(
        f"{context.project_base()}/test-cases",
        limit=limit,
        **_filters(status=status, priority=priority),
    )
    if output.is_json():
        output.emit(rows)
        return
    output.table(
        "Test cases",
        ["KEY", "TITLE", "PRIORITY", "AUTOMATION", "STATUS"],
        [
            [case["key"], case["title"], case["priority"], case["automation"], case["status"]]
            for case in rows
        ],
        empty="No test cases.",
    )


def test_runs(
    status: str | None = typer.Option(
        None, "--status", help="queued|running|passed|failed|aborted."
    ),
    environment: str | None = typer.Option(None, "--environment"),
    limit: int = typer.Option(20, "--limit", "-n", help="How many to list."),
) -> None:
    """List test runs in the selected project."""
    rows = context.client().results(
        f"{context.project_base()}/test-runs",
        limit=limit,
        **_filters(status=status, environment=environment),
    )
    if output.is_json():
        output.emit(rows)
        return
    output.table(
        "Test runs",
        ["ID", "STATUS", "TOTAL", "PASSED", "FAILED", "STARTED"],
        [
            [
                r["id"],
                r["status"],
                r["total"],
                r["passed"],
                r["failed"],
                output.timestamp(r["started_at"]) or "-",
            ]
            for r in rows
        ],
        empty="No test runs.",
    )


def bugs(
    status: str | None = typer.Option(
        None, "--status", help="open|in_progress|resolved|closed|reopened."
    ),
    severity: str | None = typer.Option(None, "--severity", help="s1|s2|s3|s4."),
    limit: int = typer.Option(50, "--limit", "-n", help="How many to list."),
) -> None:
    """List bugs in the selected project, worst first."""
    rows = context.client().results(
        f"{context.project_base()}/bugs",
        limit=limit,
        ordering="severity",
        **_filters(status=status, severity=severity),
    )
    if output.is_json():
        output.emit(rows)
        return
    output.table(
        "Bugs",
        ["KEY", "TITLE", "SEVERITY", "PRIORITY", "STATUS", "OCCURRENCES"],
        [
            [
                bug["key"],
                bug["title"],
                bug["severity"],
                bug["priority"],
                bug["status"],
                bug.get("occurrence_count", 0),
            ]
            for bug in rows
        ],
        empty="No bugs.",
    )


def releases(
    status: str | None = typer.Option(None, "--status", help="planned|released|cancelled."),
    limit: int = typer.Option(50, "--limit", "-n", help="How many to list."),
) -> None:
    """List releases in the selected project."""
    rows = context.client().results(
        f"{context.project_base()}/releases",
        limit=limit,
        **_filters(status=status),
    )
    if output.is_json():
        output.emit(rows)
        return
    output.table(
        "Releases",
        ["VERSION", "NAME", "STATUS", "PLANNED", "RELEASED"],
        [
            [
                row["version"],
                row.get("name", ""),
                row["status"],
                output.timestamp(row.get("planned_at")) or "-",
                output.timestamp(row.get("released_at")) or "-",
            ]
            for row in rows
        ],
        empty="No releases.",
    )
