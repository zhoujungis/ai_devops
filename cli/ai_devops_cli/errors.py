"""Errors the CLI reports to the user.

Two kinds, deliberately: :class:`CliError` is the user pointing the tool at
something it cannot use (no project selected, unknown slug) and is a usage
mistake; :class:`~ai_devops_cli.client.ApiError` is the server refusing, and
carries the backend's error envelope so the stable ``code`` and the
``request_id`` can be shown next to the message.
"""

from __future__ import annotations


class CliError(RuntimeError):
    """A usage error the user can fix by changing what they asked for."""
