"""Sign in, sign out, and see who you are."""

from __future__ import annotations

import typer

from ai_devops_cli import __version__, context, output
from ai_devops_cli.client import ApiError
from ai_devops_cli.config import config_path


def login(
    email: str | None = typer.Option(None, "--email", "-e", help="Account email."),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Password; prompted securely when omitted."
    ),
) -> None:
    """Exchange email + password for a stored JWT pair."""
    if not email:
        email = typer.prompt("Email")
    if not password:
        password = typer.prompt("Password", hide_input=True)

    data = context.client().login(email, password)
    output.success(f"Signed in as {data['user']['email']} against {context.settings().base_url}")


def logout() -> None:
    """Forget the local session and blacklist the refresh token."""
    context.client().logout()
    output.success("Signed out.")


def me() -> None:
    """Show the authenticated user."""
    user = context.client().get("/me")
    if output.is_json():
        output.emit(user)
        return
    output.kv(
        [
            ("email", user["email"]),
            ("name", user.get("display_name") or "-"),
            ("id", user["id"]),
            ("active", user["is_active"]),
            ("joined", user["date_joined"]),
        ]
    )


def _session_state() -> str:
    """Whether the stored token still works, checked against the API.

    A stored token can be expired or revoked, and ``status`` is where a user looks to
    find out — reporting it from local state alone would say "signed in" for a token
    the server has already rejected.
    """
    if not context.settings().logged_in:
        return "not signed in"
    try:
        user = context.client().get("/me")
    except ApiError as exc:
        return f"rejected ({exc.code})"
    return f"ok ({user['email']})"


def status() -> None:
    """Show what the CLI is currently pointed at."""
    current = context.settings()
    session = _session_state()

    if output.is_json():
        output.emit(
            {
                "base_url": current.base_url,
                "email": current.email,
                "session": session,
                "org": current.org_name,
                "org_id": current.org_id,
                "project": current.project_name,
                "project_id": current.project_id,
                "config": str(config_path()),
            }
        )
        return
    output.kv(
        [
            ("API", current.base_url),
            ("signed in as", current.email or "(nobody)"),
            ("session", session),
            ("project", context.project_label() or "(none selected)"),
            ("config file", config_path()),
        ]
    )


def version() -> None:
    """Print the CLI version."""
    output.console.print(__version__)
