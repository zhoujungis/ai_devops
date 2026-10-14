"""Upgrading the client itself.

The only command here, and the only one that deliberately touches the machine outside
the API: it hands off to the same installer that owns this copy.
"""

from __future__ import annotations

from ai_devops_cli import __version__, output, update
from ai_devops_cli.errors import CliError


def upgrade() -> None:
    """Install the newest release from PyPI."""
    if update.is_development_checkout():
        raise CliError(
            "This copy was installed from a local directory ('pip install -e'), so "
            "upgrading would replace your working tree with the published release. "
            "Update it with git pull instead."
        )

    newer = update.newer_version()
    if newer is None:
        output.success(f"Already on the newest release ({__version__}).")
        return

    command = update.upgrade_command()
    output.console.print(f"[dim]Running {' '.join(command)}[/dim]")
    if update.run_upgrade() != 0:
        raise CliError(f"Upgrade failed. Run it yourself: {' '.join(command)}")

    # The cached answer describes the version we are leaving, so it is now wrong.
    update.forget()
    output.success(f"Upgraded {__version__} -> {newer}. Run 'aidevops version' to confirm.")
