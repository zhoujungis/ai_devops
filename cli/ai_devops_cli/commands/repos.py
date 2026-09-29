"""Git connections and repositories — the commands that switch the pipeline on.

Nothing downstream (commits, modules, risk, findings) has data until a connection
is verified and a repository is registered and synced, so this is the entry point
of the whole tool.
"""

from __future__ import annotations

import time
from typing import Any

import typer

from ai_devops_cli import context, output
from ai_devops_cli.errors import CliError


def connections() -> None:
    """List git connections for the selected organization (admin only)."""
    rows = context.client().results(f"{context.org_base()}/git-connections")
    if output.is_json():
        output.emit(rows)
        return
    output.table(
        "Git connections",
        ["ID", "LABEL", "PROVIDER", "STATUS", "TOKEN", "LAST VERIFIED"],
        [
            [
                row["id"],
                row["label"],
                row["provider"],
                row["status"],
                "yes" if row["has_token"] else "no",
                output.timestamp(row["last_verified_at"]) or "never",
            ]
            for row in rows
        ],
        empty="No connections. Add one with 'copilot connection-add'.",
    )


def connection_add(
    label: str = typer.Option(..., "--label", "-l", help="A name you will recognise."),
    provider: str = typer.Option("github", "--provider", help="github|gitlab."),
    auth_type: str = typer.Option("pat", "--auth-type", help="pat|oauth|app."),
    base_url: str = typer.Option("", "--base-url", help="Leave blank for the public host."),
    token: str = typer.Option("", "--token", "-t", help="Personal access token."),
    webhook_secret: str = typer.Option("", "--webhook-secret", help="For inbound webhooks."),
) -> None:
    """Create an organization git connection."""
    body: dict[str, Any] = {
        "label": label,
        "provider": provider,
        "auth_type": auth_type,
        "base_url": base_url,
    }
    if token:
        body["token"] = token
    if webhook_secret:
        body["webhook_secret"] = webhook_secret

    row = context.client().post(f"{context.org_base()}/git-connections", json=body)
    output.success(f"Created connection {row['id']} ({row['label']}).")
    if row.get("webhook_url"):
        output.console.print(f"  webhook URL: {row['webhook_url']}")


def connection_verify(connection_id: str = typer.Argument(..., help="Connection id.")) -> None:
    """Check stored credentials against the provider."""
    result = context.client().post(f"{context.org_base()}/git-connections/{connection_id}/verify")
    if output.is_json():
        output.emit(result)
        return
    if result["verified"]:
        output.success(f"Verified ({result['status']}).")
    else:
        output.warn(f"Credentials are {result['status']}.")


def connection_delete(
    connection_id: str = typer.Argument(..., help="Connection id."),
) -> None:
    """Delete a git connection. Any repository still using it must be removed first."""
    context.client().delete(f"{context.org_base()}/git-connections/{connection_id}")
    output.success("Connection deleted.")


def repos() -> None:
    """List repositories tracked by the selected project."""
    rows = context.client().results(f"{context.project_base()}/repositories")
    if output.is_json():
        output.emit(rows)
        return
    output.table(
        "Repositories",
        ["ID", "FULL NAME", "BRANCH", "SYNC", "LAST SYNCED", "ERROR"],
        [
            [
                row["id"],
                row["full_name"],
                row["default_branch"],
                row["sync_status"],
                output.timestamp(row["last_synced_at"]) or "never",
                row["sync_error"],
            ]
            for row in rows
        ],
        empty="No repositories. Add one with 'copilot repo-add'.",
    )


def repo_add(
    connection: str = typer.Option(..., "--connection", "-c", help="Connection id."),
    full_name: str = typer.Option(..., "--full-name", "-f", help="owner/repository."),
    sync_window_days: int | None = typer.Option(None, "--window-days", help="History window."),
    module_depth: int | None = typer.Option(None, "--module-depth", help="Module tree depth."),
) -> None:
    """Register a repository so the sync can pull its history."""
    body: dict[str, Any] = {"connection": connection, "full_name": full_name}
    if sync_window_days is not None:
        body["sync_window_days"] = sync_window_days
    if module_depth is not None:
        body["module_depth"] = module_depth

    row = context.client().post(f"{context.project_base()}/repositories", json=body)
    output.success(f"Registered {row['full_name']} ({row['id']}). Now run: copilot sync {row['id']}")


def repo_remove(repository_id: str = typer.Argument(..., help="Repository id.")) -> None:
    """Stop tracking a repository."""
    context.client().delete(f"{context.project_base()}/repositories/{repository_id}")
    output.success("Repository removed.")


def repo_update(
    repository_id: str = typer.Argument(..., help="Repository id."),
    sync_window_days: int | None = typer.Option(None, "--window-days", help="History window."),
    module_depth: int | None = typer.Option(None, "--module-depth", help="Module tree depth."),
) -> None:
    """Change how much history a repository keeps and how finely it is split."""
    body: dict[str, Any] = {}
    if sync_window_days is not None:
        body["sync_window_days"] = sync_window_days
    if module_depth is not None:
        body["module_depth"] = module_depth
    if not body:
        # An empty PATCH would look like it worked while changing nothing.
        raise CliError("Nothing to change: pass --window-days and/or --module-depth.")

    row = context.client().patch(
        f"{context.project_base()}/repositories/{repository_id}", json=body
    )
    output.success(f"Updated {row['full_name']}.")


def sync(
    repository_id: str = typer.Argument(..., help="Repository id."),
    wait: bool = typer.Option(False, "--wait", "-w", help="Poll until the sync finishes."),
    interval: float = typer.Option(2.0, "--interval", help="Seconds between polls."),
    timeout: float = typer.Option(1800.0, "--timeout", help="Give up after this many seconds."),
) -> None:
    """Queue a synchronisation run and, with --wait, follow it to completion."""
    client = context.client()
    base = f"{context.project_base()}/repositories/{repository_id}"
    queued = client.post(f"{base}/sync")
    if output.is_json() and not wait:
        output.emit(queued)
        return
    output.success(f"Sync queued (task {queued.get('task_id', '?')}).")

    if not wait:
        return

    deadline = time.monotonic() + timeout
    with output.console.status("Syncing…") as status:
        while True:
            row = client.get(base)
            sync_status = row["sync_status"]
            status.update(f"Syncing… ({sync_status})")
            if sync_status in {"succeeded", "failed", "idle"}:
                break
            if time.monotonic() >= deadline:
                raise CliError(f"Sync did not finish within {timeout:.0f}s.")
            time.sleep(interval)

    if row["sync_status"] == "succeeded":
        output.success(f"Synced {row['full_name']}.")
    else:
        output.warn(f"Sync ended as {row['sync_status']}: {row['sync_error'] or '(no detail)'}")
