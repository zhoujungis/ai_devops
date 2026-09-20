"""Chooses the implementation behind :class:`GitProvider`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from apps.integrations.git.github import GitHubProvider

if TYPE_CHECKING:
    from apps.integrations.git.base import GitProvider


def provider_for(connection: Any, **kwargs: Any) -> GitProvider:
    """Build a provider for a stored connection, decrypting its token.

    Provider-specific configuration lives in ``connection.base_url`` and the
    encrypted token, so callers never branch on the provider themselves.
    """
    if connection.provider != "github":
        raise ValueError(f"Unsupported git provider: {connection.provider!r}")

    token = connection.token or None
    base_url = connection.base_url or "https://api.github.com"
    return GitHubProvider(token=token, base_url=base_url, **kwargs)
