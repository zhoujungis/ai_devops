"""Command-line entry point.

Commands are grouped by the question they answer, not by the backend app they
happen to live in, because the thing being explored is a project — not a set of
REST resources. Every command supports ``--json``, which is what makes the tool
composable with ``jq`` and shell scripts.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Any

import typer

from ai_devops_cli import context, output
from ai_devops_cli.client import ApiError
from ai_devops_cli.commands import ai, audit, auth, code, providers, repos, scope, tracker
from ai_devops_cli.errors import CliError

HELP = (
    "Terminal client for the AI DevOps / QA Copilot API.\n\n"
    "Typical first run:\n"
    "  copilot login\n"
    "  copilot projects\n"
    "  copilot use <project-slug>\n"
    "  copilot commits\n"
    "  copilot explain <sha>\n"
    "  copilot findings\n\n"
    "'--json' is a global option: put it before the command, as in "
    "'copilot --json findings' (or set COPILOT_JSON=1)."
)

app = typer.Typer(no_args_is_help=True, add_completion=True, help=HELP)


@app.callback()
def _root(
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON instead of tables."),
    base_url: str | None = typer.Option(None, "--base-url", help="Override the stored API base URL."),
) -> None:
    """Configure the shared runtime before any command runs."""
    from_env = os.environ.get("COPILOT_JSON", "").strip().lower() in {"1", "true", "yes", "on"}
    output.set_json(json_output or from_env)
    context.configure(base_url=base_url)


def _command(name: str, summary: str) -> Callable[[Callable[..., Any]], None]:
    """Register a command with uniform error handling.

    The wrapper turns an :class:`ApiError` or :class:`CliError` into one clean line and
    exit code 1. It lives on the command itself rather than on ``Typer.__call__``
    because ``CliRunner`` builds the Click command and calls its ``.main()`` directly —
    a ``__call__`` override is skipped there, so the tested path and the shipped path
    would otherwise behave differently. ``functools.wraps`` keeps the signature, which
    is what Typer introspects to build the options.
    """

    def register(func: Callable[..., Any]) -> None:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except (ApiError, CliError) as exc:
                output.fail(exc)
                # SystemExit, not typer.Exit: Click only converts its own Exit while it
                # owns the call; by now the exception has left the callback, so a
                # typer.Exit would surface as a traceback after the clean message.
                raise SystemExit(1) from exc

        app.command(name, help=summary)(wrapper)

    return register


def tui() -> None:
    """Launch the full-screen Textual dashboard."""
    # Imported lazily so the (heavy) TUI dependency is not paid for by every
    # one-shot command.
    from ai_devops_cli.tui.app import run

    run()


# --- session ---------------------------------------------------------------
_command("login", "Sign in and store a token pair.")(auth.login)
_command("logout", "Forget the local session.")(auth.logout)
_command("me", "Show the authenticated user.")(auth.me)
_command("status", "Show what the CLI is pointed at.")(auth.status)
_command("version", "Print the CLI version.")(auth.version)

# --- scope -----------------------------------------------------------------
_command("orgs", "List your organizations.")(scope.orgs)
_command("projects", "List projects across your organizations.")(scope.projects)
_command("use", "Select the current project.")(scope.use)
_command("unuse", "Clear the current project.")(scope.unuse)

# --- code ------------------------------------------------------------------
_command("commits", "List recent commits.")(code.commits)
_command("commit", "Show one commit.")(code.commit)
_command("explain", "Walk the correlation chain for a commit.")(code.explain)
_command("risk", "Risk score for a commit.")(code.risk)
_command("modules", "List modules.")(code.modules)
_command("module-risk", "Risk score for a module.")(code.module_risk)

# --- AI --------------------------------------------------------------------
_command("findings", "List AI findings.")(ai.findings)
_command("finding", "Show one finding.")(ai.finding)
_command("triage", "Set an AI finding's status.")(ai.triage)
_command("proposals", "List AI proposals.")(ai.proposals)
_command("approve", "Confirm a proposal and run its executor.")(ai.approve)
_command("reject", "Decline a proposal.")(ai.reject)
_command("agents", "List analysis agents.")(ai.agents)
_command("analyse", "Queue an analysis.")(ai.analyse)
_command("analyses", "List past analysis jobs.")(ai.analyses)
_command("job", "Show an analysis job.")(ai.job)
_command("trace", "Show what an analysis did (model runs and tool calls).")(ai.trace)

# --- git integration -------------------------------------------------------
_command("connections", "List git connections.")(repos.connections)
_command("connection-add", "Create a git connection.")(repos.connection_add)
_command("connection-verify", "Verify stored credentials.")(repos.connection_verify)
_command("connection-delete", "Delete a git connection.")(repos.connection_delete)
_command("repos", "List repositories.")(repos.repos)
_command("repo-add", "Register a repository.")(repos.repo_add)
_command("repo-update", "Change a repository's sync settings.")(repos.repo_update)
_command("repo-remove", "Stop tracking a repository.")(repos.repo_remove)
_command("sync", "Queue a repository sync.")(repos.sync)

# --- model credentials -----------------------------------------------------
_command("providers", "List AI provider credentials.")(providers.list_providers)
_command("provider-add", "Register a model endpoint.")(providers.add_provider)
_command("provider-verify", "Check a model endpoint's credentials.")(providers.verify_provider)
_command("provider-remove", "Delete a model endpoint.")(providers.remove_provider)

# --- trackers --------------------------------------------------------------
_command("requirements", "List requirements.")(tracker.requirements)
_command("test-cases", "List test cases.")(tracker.test_cases)
_command("test-runs", "List test runs.")(tracker.test_runs)
_command("bugs", "List bugs.")(tracker.bugs)
_command("releases", "List releases.")(tracker.releases)

# --- audit -----------------------------------------------------------------
_command("audit", "Show the organization's audit trail.")(audit.list_audit)

# --- interactive -----------------------------------------------------------
_command("tui", "Open the interactive terminal dashboard.")(tui)
