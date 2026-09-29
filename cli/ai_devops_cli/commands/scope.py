"""Organizations, projects, and choosing which project the CLI works in.

Every project-scoped endpoint needs an organization id and a project id in the
URL. Rather than make the user pass both to every command, ``use`` resolves a
project once and stores the two ids; the ``projects`` listing is the discovery
step for it.
"""

from __future__ import annotations

import typer

from ai_devops_cli import context, output
from ai_devops_cli.errors import CliError


def orgs() -> None:
    """List the organizations you belong to."""
    rows = context.client().results("/orgs")
    if output.is_json():
        output.emit(rows)
        return
    output.table(
        "Organizations",
        ["SLUG", "NAME", "PLAN", "ROLE", "ID"],
        [[o["slug"], o["name"], o.get("plan", ""), o.get("role") or "-", o["id"]] for o in rows],
    )


def projects() -> None:
    """List every project across your organizations."""
    client = context.client()
    current = context.settings()
    rows: list[list[str]] = []
    payload: list[dict[str, object]] = []

    for org in client.results("/orgs"):
        for project in client.results(f"/orgs/{org['id']}/projects"):
            marker = "*" if project["id"] == current.project_id else " "
            rows.append(
                [
                    marker,
                    project["slug"],
                    project["name"],
                    org["slug"],
                    project.get("role") or "-",
                    project["id"],
                ]
            )
            payload.append({**project, "org_slug": org["slug"]})

    if output.is_json():
        output.emit(payload)
        return
    output.table(
        "Projects (* = selected)",
        ["", "SLUG", "NAME", "ORG", "ROLE", "ID"],
        rows,
        empty="You have no projects yet.",
    )


def use(slug: str = typer.Argument(..., help="Project slug, name, or id.")) -> None:
    """Select the project that project-scoped commands act on."""
    client = context.client()
    found: tuple[dict[str, object], dict[str, object]] | None = None

    for org in client.results("/orgs"):
        for project in client.results(f"/orgs/{org['id']}/projects"):
            if slug in {project["slug"], project["name"], project["id"]}:
                found = (org, project)
                break
        if found:
            break

    if found is None:
        raise CliError(f"No project matching {slug!r}. Run 'copilot projects'.")

    org, project = found
    current = context.settings()
    current.org_id = str(org["id"])
    current.org_name = str(org["slug"])
    current.project_id = str(project["id"])
    current.project_name = str(project["slug"])
    from ai_devops_cli.config import save

    save(current)
    output.success(f"Now using {org['slug']}/{project['slug']}.")


def unuse() -> None:
    """Clear the selected project."""
    current = context.settings()
    current.clear_project()
    from ai_devops_cli.config import save

    save(current)
    output.success("Project selection cleared.")
