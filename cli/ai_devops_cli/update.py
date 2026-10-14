"""Noticing that a newer release exists, and installing it.

Two rules shape this module. The check must never break or stall a command, so it is
cached on disk, bounded by a short timeout, and every failure is swallowed. And it must
never pollute machine-readable output, so in ``--json`` mode the hint goes to stderr --
a script piping stdout into ``jq`` should not have to defend itself against a nudge.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any

import httpx
import platformdirs
from packaging.version import InvalidVersion, Version

from ai_devops_cli import __version__, output
from ai_devops_cli.config import APP_NAME

PACKAGE_NAME = "aidevops"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"

#: A day per lookup keeps this to one request per machine per day, which is invisible
#: next to the commands it runs in front of.
CACHE_TTL_SECONDS = 24 * 60 * 60

#: Short on purpose: this runs before whatever the user actually asked for, and a slow
#: version check is a worse experience than no version check.
CHECK_TIMEOUT_SECONDS = 3.0

#: Truthy disables the check entirely. CI and the test suite set it, so a command never
#: depends on PyPI being reachable.
DISABLE_ENV = "AIDEVOPS_NO_UPDATE_CHECK"

#: Commands that do their own version work, and so should not be preceded by a hint
#: telling the user to run one of them.
SKIP_CHECK_COMMANDS = frozenset({"upgrade"})


def cache_path() -> Path:
    override = os.environ.get("AIDEVOPS_UPDATE_CACHE")
    if override:
        return Path(override)
    # appauthor=False for the same reason as the config directory: Windows would
    # otherwise repeat the app name as the author and nest aidevops/aidevops.
    return Path(platformdirs.user_cache_dir(APP_NAME, appauthor=False)) / "update-check.json"


def disabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def fetch_latest(*, transport: httpx.BaseTransport | None = None) -> str | None:
    """The newest version on PyPI, or ``None`` if it cannot be determined.

    Returning ``None`` instead of raising is the whole point: being offline is not an
    error state for a user who only wants to list commits.
    """
    try:
        with httpx.Client(timeout=CHECK_TIMEOUT_SECONDS, transport=transport) as client:
            response = client.get(PYPI_JSON_URL, headers={"Accept": "application/json"})
            response.raise_for_status()
            latest = response.json()["info"]["version"]
    except Exception:
        return None
    return latest if isinstance(latest, str) else None


def _read_cache() -> dict[str, Any]:
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(latest: str) -> None:
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"latest": latest, "checked_at": time.time()}), encoding="utf-8")
    except OSError:
        # An unwritable cache costs one lookup per run; it is not a failure worth
        # reporting, and certainly not one worth failing a command over.
        pass


def forget() -> None:
    """Drop the cached answer so the next check asks PyPI again."""
    with contextlib.suppress(OSError):
        cache_path().unlink()


def _cached_latest(*, clock: Callable[[], float]) -> str | None:
    """The cached version, or ``None`` when there is not a fresh one to trust."""
    cached = _read_cache()
    version = cached.get("latest")
    checked_at = cached.get("checked_at")
    if not isinstance(version, str) or not isinstance(checked_at, int | float):
        return None
    if clock() - checked_at >= CACHE_TTL_SECONDS:
        return None
    return version


def newer_version(
    *,
    fetcher: Callable[[], str | None] = fetch_latest,
    clock: Callable[[], float] = time.time,
) -> str | None:
    """The newer release, if there is one, else ``None``.

    Normally a file read: only a cold or aged-out cache reaches the network.
    """
    if disabled():
        return None

    latest = _cached_latest(clock=clock)
    if latest is None:
        latest = fetcher()
        if latest is None:
            return None
        _write_cache(latest)

    return latest if _is_newer(latest, __version__) else None


def _is_newer(candidate: str, current: str) -> bool:
    """True when ``candidate`` is a later release than ``current``.

    An unparseable version compares as "not newer", so a local build with an odd
    version string never nags its own author.
    """
    try:
        return Version(candidate) > Version(current)
    except InvalidVersion:
        return False


def hint() -> None:
    """Print a red one-line nudge when a newer release exists."""
    try:
        newer = newer_version()
    except Exception:
        return
    if newer is None:
        return

    message = (
        f"aidevops {newer} is available (you have {__version__}). "
        f"Run 'aidevops upgrade' to install it."
    )
    # In --json mode stdout belongs to the payload, so the hint goes to stderr: still
    # visible to a human, invisible to whatever is parsing stdout.
    console = output.err_console if output.is_json() else output.console
    console.print(f"[bold red]{message}[/bold red]")


def is_development_checkout() -> bool:
    """True when this copy was installed from a local directory.

    ``pip install -e`` points the entry point at a working tree, so "upgrading" would
    quietly replace that checkout with the published release and lose the local state
    the user is working on.
    """
    try:
        raw = distribution(PACKAGE_NAME).read_text("direct_url.json")
    except (PackageNotFoundError, OSError):
        return False
    if not raw:
        return False
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(info, dict):
        return False
    return bool(info.get("dir_info", {}).get("editable"))


def upgrade_command() -> list[str]:
    """The right way to upgrade *this* installation.

    pipx gives each tool its own virtualenv, so running pip inside one is both
    pointless and likely to be undone by the next ``pipx upgrade``.
    """
    if "pipx" in sys.prefix.lower() or "pipx" in sys.base_prefix.lower():
        return ["pipx", "upgrade", PACKAGE_NAME]
    return [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE_NAME]


def run_upgrade() -> int:
    """Hand off to the installer and report how it went."""
    return subprocess.run(upgrade_command(), check=False).returncode
