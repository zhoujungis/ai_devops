"""Rendering.

Every command produces rows and hands them here. With the global ``--json`` flag the
raw payload is printed instead, which is what makes the CLI scriptable: a shell can
pipe ``copilot --json findings`` into ``jq`` without the table code getting in the way.
"""

from __future__ import annotations

import contextlib
import json
import sys
from typing import Any

from rich.console import Console
from rich.table import Table

from ai_devops_cli.client import ApiError
from ai_devops_cli.errors import CliError


def _force_utf8() -> None:
    """Make stdout/stderr UTF-8 so real data can be printed.

    A Windows console defaults to a legacy code page; this one is cp1252, which
    cannot encode CJK at all — printing a commit authored by someone with a Chinese
    name would crash the command. UTF-8 is what the terminals people actually use
    expect, and the operation is a no-op where it is unsupported.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8")


_force_utf8()

console = Console()
err_console = Console(stderr=True)

_json = False


def set_json(enabled: bool) -> None:
    global _json
    _json = enabled


def is_json() -> bool:
    return _json


def emit(data: Any) -> None:
    """Print the raw payload (``--json`` mode)."""
    console.print_json(json.dumps(data, default=str))


def table(
    title: str,
    headers: list[str],
    rows: list[list[Any]],
    *,
    empty: str = "Nothing to show.",
) -> None:
    if not rows:
        console.print(f"[dim]{empty}[/dim]")
        return
    grid = Table(title=title, header_style="bold", title_justify="left", pad_edge=False)
    for header in headers:
        grid.add_column(header, overflow="fold")
    for row in rows:
        grid.add_row(*("" if cell is None else str(cell) for cell in row))
    console.print(grid)


def kv(pairs: list[tuple[str, Any]]) -> None:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold dim", justify="right")
    grid.add_column()
    for key, value in pairs:
        grid.add_row(key, "" if value is None else str(value))
    console.print(grid)


def success(message: str) -> None:
    # ASCII markers throughout: a Windows console on cp1252 or GBK cannot encode
    # glyphs like U+2713, and a CLI that crashes printing a tick mark is worse than
    # one that prints a plain "+".
    console.print(f"[green]+[/green] {message}")


def warn(message: str) -> None:
    err_console.print(f"[yellow]![/yellow] {message}")


def fail(exc: Exception) -> None:
    """Report an error without a traceback, the way a CLI user expects."""
    if isinstance(exc, ApiError):
        err_console.print(f"[red]x {exc.message}[/red]")
        if exc.status:
            err_console.print(f"  [dim]code={exc.code} status={exc.status}[/dim]")
        if exc.details:
            err_console.print(f"  [dim]details={json.dumps(exc.details, default=str)}[/dim]")
        if exc.request_id:
            err_console.print(f"  [dim]request_id={exc.request_id}[/dim]")
    elif isinstance(exc, CliError):
        err_console.print(f"[red]x {exc}[/red]")
    else:
        err_console.print(f"[red]x {type(exc).__name__}: {exc}[/red]")
    sys.stdout.flush()


def first_line(text: str | None) -> str:
    return (text or "").splitlines()[0] if text else ""


def timestamp(value: Any) -> str:
    """Trim an ISO-8601 timestamp to ``YYYY-MM-DD HH:MM`` for table display.

    The precision the API returns (seconds, microseconds, offset) is wasted width in
    a table and is what forces neighbouring columns to wrap.
    """
    if not value:
        return ""
    text = str(value)
    date, separator, rest = text.partition("T")
    if not separator:
        return text
    return f"{date} {rest[:5]}"
