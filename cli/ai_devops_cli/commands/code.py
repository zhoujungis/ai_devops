"""Commits, modules, the correlation chain, and the risk breakdown.

These are the commands that answer the product's core question — "what does this
change touch, and how risky is it" — so they render the full decomposition rather
than a summary number.
"""

from __future__ import annotations

from typing import Any

import typer

from ai_devops_cli import context, output
from ai_devops_cli.client import Client
from ai_devops_cli.errors import CliError


def _resolve_commit(client: Client, ref: str) -> dict[str, Any]:
    """Find a commit by (short) sha.

    There is no by-sha route, so this uses the search filter and then insists on a
    real sha match — search also hits the message and author, and picking the first
    hit would silently act on the wrong commit.
    """
    rows = client.results(f"{context.project_base()}/commits", search=ref)
    for commit in rows:
        full = str(commit["sha"])
        if full == ref or full.startswith(ref) or commit["short_sha"] == ref:
            return commit
    raise CliError(f"No commit matching {ref!r} in {context.project_label()}.")


def _render_assessment(assessment: dict[str, Any]) -> None:
    output.console.print(
        f"[bold]{assessment['score']:.1f}[/bold] / 100  "
        f"[bold]{str(assessment['level']).upper()}[/bold]"
    )
    rows = sorted(assessment["breakdown"], key=lambda row: row["contribution"], reverse=True)
    output.table(
        "Risk breakdown",
        ["SIGNAL", "WHAT WE MEASURED", "SHARE", "CONTRIBUTION"],
        [
            [
                row["label"],
                row["detail"],
                f"{row['normalized'] * 100:.0f}%",
                f"{row['contribution']:.2f}",
            ]
            for row in rows
        ],
    )


def commits(
    limit: int = typer.Option(20, "--limit", "-n", help="How many commits to list."),
    repository: str | None = typer.Option(None, "--repository", help="Filter by repository id."),
    author: str | None = typer.Option(None, "--author", help="Filter by author email."),
) -> None:
    """List recent commits in the selected project."""
    params: dict[str, Any] = {"ordering": "-committed_at"}
    if repository:
        params["repository"] = repository
    if author:
        params["author_email"] = author

    rows = context.client().results(f"{context.project_base()}/commits", limit=limit, **params)
    if output.is_json():
        output.emit(rows)
        return
    output.table(
        "Commits",
        ["SHA", "WHEN", "MESSAGE", "AUTHOR", "+/-"],
        [
            [
                commit["short_sha"],
                output.timestamp(commit["committed_at"]),
                output.first_line(commit["message"]),
                commit["author_name"],
                f"+{commit['additions']}/-{commit['deletions']}",
            ]
            for commit in rows
        ],
        empty="No commits. Register and sync a repository first.",
    )


def commit(sha: str = typer.Argument(..., help="Commit sha (full or short).")) -> None:
    """Show one commit and the files it changed."""
    client = context.client()
    found = _resolve_commit(client, sha)
    detail = client.get(f"{context.project_base()}/commits/{found['id']}")

    if output.is_json():
        output.emit(detail)
        return

    output.kv(
        [
            ("sha", detail["sha"]),
            ("when", output.timestamp(detail["committed_at"])),
            ("author", f"{detail['author_name']} <{detail['author_email']}>"),
            ("message", output.first_line(detail["message"])),
            ("churn", f"+{detail['additions']} / -{detail['deletions']}"),
            ("files", detail["files_changed"]),
        ]
    )
    output.table(
        "Modules touched",
        ["MODULE", "PATH", "CHURN", "WEIGHT", "TEST"],
        [
            [
                impact["module"]["name"],
                impact["module"]["path_prefix"],
                impact["churn_lines"],
                impact["weight"],
                "yes" if impact["is_test_change"] else "",
            ]
            for impact in detail.get("module_impacts", [])
        ],
        empty="No module attribution for this commit.",
    )
    output.table(
        "Files",
        ["PATH", "CHANGE", "+/-", "PATCH"],
        [
            [
                item["path"],
                item["change_type"],
                f"+{item['additions']}/-{item['deletions']}",
                "yes" if item["has_patch"] else "no",
            ]
            for item in detail.get("files", [])
        ],
    )


def explain(sha: str = typer.Argument(..., help="Commit sha (full or short).")) -> None:
    """Walk the full correlation chain for a commit."""
    client = context.client()
    found = _resolve_commit(client, sha)
    chain = client.get(f"{context.project_base()}/commits/{found['id']}/explain")

    if output.is_json():
        output.emit(chain)
        return

    requirement = chain.get("requirement")
    output.kv(
        [
            (
                "commit",
                f"{chain['commit']['short_sha']} {output.first_line(chain['commit']['message'])}",
            ),
            (
                "requirement",
                (
                    f"{requirement['external_key']} {requirement['title']} "
                    f"(via {chain['requirement_source']})"
                    if requirement
                    else "(none resolved)"
                ),
            ),
            ("releases", ", ".join(r["version"] for r in chain["releases"]) or "(none)"),
        ]
    )

    output.table(
        "Modules touched",
        ["PATH", "WEIGHT", "CHURN", "FILES", "TEST"],
        [
            [
                item["module"]["path_prefix"],
                item["weight"],
                item["churn_lines"],
                item["file_count"],
                "yes" if item["is_test_change"] else "",
            ]
            for item in chain["modules"]
        ],
    )

    output.table(
        "Regression candidates",
        ["KEY", "TITLE", "SCORE", "WHY"],
        [
            [
                candidate["test_case"]["key"],
                candidate["test_case"]["title"],
                f"{candidate['score']:.3f}",
                "; ".join(candidate["reasons"]),
            ]
            for candidate in chain["regression_candidates"]
        ],
        empty="No test cases are linked to the touched modules.",
    )

    output.table(
        "Historical bugs",
        ["KEY", "TITLE", "SEVERITY", "STATUS", "SHARED MODULES"],
        [
            [
                entry["bug"]["key"],
                entry["bug"]["title"],
                entry["bug"]["severity"],
                entry["bug"]["status"],
                ", ".join(entry["shared_modules"]),
            ]
            for entry in chain["historical_bugs"]
        ],
        empty="No open bugs are linked to the touched modules.",
    )

    for gap in chain.get("data_gaps", []):
        output.warn(gap)


def risk(sha: str = typer.Argument(..., help="Commit sha (full or short).")) -> None:
    """Show the risk score for a commit, decomposed by signal."""
    client = context.client()
    found = _resolve_commit(client, sha)
    assessment = client.get(f"{context.project_base()}/commits/{found['id']}/risk")

    if output.is_json():
        output.emit(assessment)
        return
    _render_assessment(assessment)


def modules(
    limit: int = typer.Option(50, "--limit", "-n", help="How many modules to list."),
) -> None:
    """List the modules resolved for the project."""
    rows = context.client().results(f"{context.project_base()}/modules", limit=limit)
    if output.is_json():
        output.emit(rows)
        return
    output.table(
        "Modules",
        ["PATH", "NAME", "KIND", "LANG", "CENTRALITY"],
        [
            [m["path_prefix"], m["name"], m["kind"], m["language"], m["centrality_score"]]
            for m in rows
        ],
        empty="No modules yet. Sync a repository to resolve them.",
    )


def module_risk(module_id: str = typer.Argument(..., help="Module id.")) -> None:
    """Show the risk score for a module, decomposed by signal."""
    assessment = context.client().get(f"{context.project_base()}/modules/{module_id}/risk")
    if output.is_json():
        output.emit(assessment)
        return
    _render_assessment(assessment)
