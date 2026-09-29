"""Per-invocation runtime state.

A command needs three things: the settings on disk, an HTTP client bound to them,
and the URL prefix of the currently-selected project. Resolving them here keeps
every command free of loading and validation boilerplate.
"""

from __future__ import annotations

from ai_devops_cli.client import Client
from ai_devops_cli.config import Settings, load
from ai_devops_cli.errors import CliError

_settings: Settings | None = None
_client: Client | None = None
_base_url_override: str | None = None


def configure(*, base_url: str | None = None) -> None:
    """Reset state for a fresh invocation. Called from the root callback."""
    global _settings, _client, _base_url_override
    _settings = None
    _client = None
    _base_url_override = base_url


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load()
        if _base_url_override:
            _settings.base_url = _base_url_override
    return _settings


def client() -> Client:
    global _client
    if _client is None:
        _client = Client(settings())
    return _client


def require_login() -> Settings:
    current = settings()
    if not current.logged_in:
        raise CliError("Not signed in. Run: copilot login")
    return current


def org_base() -> str:
    """The URL prefix for organization-scoped endpoints (git connections, providers)."""
    current = require_login()
    if not current.org_id:
        raise CliError(
            "No organization selected. Run 'copilot projects', then 'copilot use <slug>'."
        )
    return f"/orgs/{current.org_id}"


def project_base() -> str:
    """The URL prefix shared by every project-scoped endpoint."""
    current = require_login()
    if not current.has_project:
        raise CliError(
            "No project selected. Run 'copilot projects' to list them, "
            "then 'copilot use <slug>'."
        )
    return f"/orgs/{current.org_id}/projects/{current.project_id}"


def project_label() -> str:
    current = settings()
    if not current.has_project:
        return ""
    return f"{current.org_name}/{current.project_name}"
