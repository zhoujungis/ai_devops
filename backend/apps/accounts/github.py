"""Verifying a GitHub access token and resolving the identity behind it.

The device flow hands the CLI an opaque ``access_token``; this module is the one
place that turns it into the facts we store — the immutable ``id``, the ``login``,
the avatar and an email address we are allowed to persist.

Only the two endpoints the login needs are implemented, and the HTTP transport is
injectable so tests drive recorded responses instead of the network (the same
seam :class:`apps.integrations.git.github.GitHubProvider` uses).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

GITHUB_API_BASE_URL = "https://api.github.com"
API_VERSION = "2022-11-28"

#: Address GitHub itself hands out for accounts that keep their email private.
NOREPLY_DOMAIN = "users.noreply.github.com"


class GitHubError(RuntimeError):
    """Base class for failures talking to GitHub."""


class GitHubAuthError(GitHubError):
    """GitHub rejected the access token."""


class GitHubUnavailableError(GitHubError):
    """GitHub could not be reached, or answered with an unexpected failure."""


@dataclass(frozen=True)
class GitHubIdentity:
    """The subset of a GitHub account the login needs."""

    github_id: int
    login: str
    email: str
    avatar_url: str = ""

    @property
    def noreply_email(self) -> str:
        """A stable, unique address for accounts that expose no usable email."""
        return f"{self.github_id}+{self.login}@{NOREPLY_DOMAIN}"


class GitHubOAuthClient:
    """A minimal client over ``GET /user`` and ``GET /user/emails``."""

    def __init__(
        self,
        *,
        api_base_url: str = GITHUB_API_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._client = httpx.Client(
            base_url=api_base_url.rstrip("/"),
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "ai-devops-qa-copilot",
            },
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubOAuthClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _get(self, path: str, access_token: str) -> Any:
        try:
            response = self._client.get(path, headers={"Authorization": f"Bearer {access_token}"})
        except httpx.HTTPError as exc:
            raise GitHubUnavailableError(f"Cannot reach GitHub for {path}.") from exc

        if response.status_code == 401:
            raise GitHubAuthError("GitHub rejected the access token.")
        if response.status_code >= 400:
            raise GitHubUnavailableError(f"GitHub returned {response.status_code} for {path}.")
        return response.json()

    def resolve_identity(self, access_token: str) -> GitHubIdentity:
        """Turn a token into an identity, guaranteeing a usable email.

        Three tiers, because a user who keeps their email private gets ``null`` from
        ``/user`` and this model needs a non-null, unique address:

        1. the public ``email`` on ``/user``;
        2. otherwise ``/user/emails`` (needs the ``user:email`` scope), primary and
           verified only;
        3. otherwise a synthesised ``{id}+{login}@users.noreply.github.com``.
        """
        user = self._get("/user", access_token)
        github_id = int(user["id"])
        login = str(user.get("login") or "")
        avatar_url = str(user.get("avatar_url") or "")

        email = str(user.get("email") or "").strip()
        if not email:
            email = self._primary_email(access_token)

        identity = GitHubIdentity(
            github_id=github_id, login=login, email=email, avatar_url=avatar_url
        )
        if not identity.email:
            # ``frozen=True`` forbids mutation, so rebuild with the fallback address.
            identity = GitHubIdentity(
                github_id=github_id,
                login=login,
                email=identity.noreply_email,
                avatar_url=avatar_url,
            )
        return identity

    def _primary_email(self, access_token: str) -> str:
        """The primary verified address from ``/user/emails``, or ``""``.

        A token without the ``user:email`` scope gets a 404 here; that is not an
        error worth failing the login over, so it folds into "no email found" and the
        caller falls through to the synthesised address.
        """
        try:
            emails = self._get("/user/emails", access_token)
        except GitHubError:
            return ""
        if not isinstance(emails, list):
            return ""
        for entry in emails:
            if isinstance(entry, dict) and entry.get("primary") and entry.get("verified"):
                return str(entry.get("email") or "").strip()
        return ""


def identity_from_token(access_token: str) -> GitHubIdentity:
    """Resolve a token using a default client.

    A module-level function so the view depends on one name that tests can replace
    without reaching for a real network call.
    """
    with GitHubOAuthClient() as client:
        return client.resolve_identity(access_token)
