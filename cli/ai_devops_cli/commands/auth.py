"""Sign in with GitHub, sign out, and see who you are."""

from __future__ import annotations

import contextlib
import webbrowser

import typer

from ai_devops_cli import __version__, context, output
from ai_devops_cli.client import ApiError, GitHubDeviceFlow
from ai_devops_cli.config import config_path
from ai_devops_cli.errors import CliError


def login(
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Print the code only; do not try to open a browser (useful over SSH/containers).",
    ),
) -> None:
    """Sign in with GitHub using the OAuth device flow."""
    client = context.client()

    config = client.github_config()
    client_id = str(config.get("client_id") or "")
    if not client_id:
        raise CliError(
            "This server has no GitHub OAuth app configured (GITHUB_CLIENT_ID is empty). "
            "Ask its operator to set one before signing in."
        )
    scopes = [str(scope) for scope in (config.get("scopes") or [])] or ["read:user", "user:email"]

    flow = GitHubDeviceFlow()
    try:
        code = flow.request_device_code(client_id, scopes)
        output.console.print(
            f"Open [bold]{code.verification_uri}[/bold] and enter code "
            f"[bold cyan]{code.user_code}[/bold cyan]"
        )
        if not no_browser:
            # Best-effort: a headless host has no browser and that must not be fatal.
            with contextlib.suppress(Exception):
                webbrowser.open(code.verification_uri)
        output.console.print("Waiting for authorisation...")
        access_token = flow.poll_access_token(
            client_id,
            code.device_code,
            interval=code.interval,
            expires_in=code.expires_in,
        )
    finally:
        flow.close()

    data = client.login_with_github(access_token)
    user = data.get("user") or {}
    who = user.get("github_login") or user.get("email") or "GitHub user"
    output.success(f"Signed in as {who} against {context.settings().base_url}")


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
            ("github", user.get("github_login") or "-"),
            ("email", user.get("email") or "-"),
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
    return f"ok ({user.get('github_login') or user.get('email')})"


def status() -> None:
    """Show what the CLI is currently pointed at."""
    current = context.settings()
    session = _session_state()

    if output.is_json():
        output.emit(
            {
                "base_url": current.base_url,
                "github_login": current.github_login,
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
            ("signed in as", current.github_login or current.email or "(nobody)"),
            ("session", session),
            ("project", context.project_label() or "(none selected)"),
            ("config file", config_path()),
        ]
    )


def version() -> None:
    """Print the CLI version."""
    output.console.print(__version__)
