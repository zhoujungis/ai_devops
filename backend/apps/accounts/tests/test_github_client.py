"""The GitHub token → identity mapping, driven by mocked HTTP, never the network.

These pin the three-tier email fallback, which is the part most likely to break
silently: a user who keeps their email private must still be able to sign in.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from apps.accounts.github import GitHubAuthError, GitHubOAuthClient, GitHubUnavailableError


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> GitHubOAuthClient:
    return GitHubOAuthClient(transport=httpx.MockTransport(handler))


def test_the_public_profile_email_is_used_directly() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(
            200,
            json={
                "id": 7,
                "login": "octocat",
                "email": "octocat@example.com",
                "avatar_url": "https://avatars.example/u/7",
            },
        )

    identity = _client(handler).resolve_identity("tok")

    assert identity.github_id == 7
    assert identity.login == "octocat"
    assert identity.email == "octocat@example.com"
    assert identity.avatar_url == "https://avatars.example/u/7"
    # The public email is enough; /user/emails is never called.
    assert paths == ["/user"]


def test_a_private_email_falls_back_to_the_primary_verified_one() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/emails":
            return httpx.Response(
                200,
                json=[
                    {"email": "secondary@example.com", "primary": False, "verified": True},
                    {"email": "primary@example.com", "primary": True, "verified": True},
                ],
            )
        return httpx.Response(
            200, json={"id": 7, "login": "octocat", "email": None, "avatar_url": ""}
        )

    identity = _client(handler).resolve_identity("tok")

    assert identity.email == "primary@example.com"


def test_an_unverified_primary_email_is_ignored() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/emails":
            return httpx.Response(
                200,
                json=[{"email": "unverified@example.com", "primary": True, "verified": False}],
            )
        return httpx.Response(200, json={"id": 7, "login": "octocat", "email": None})

    identity = _client(handler).resolve_identity("tok")

    assert identity.email == "7+octocat@users.noreply.github.com"


def test_a_noreply_address_is_synthesised_when_no_email_is_available() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/emails":
            # Empty list: a token that somehow has no primary verified address.
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"id": 42, "login": "ghost", "email": None})

    identity = _client(handler).resolve_identity("tok")

    assert identity.email == "42+ghost@users.noreply.github.com"
    assert identity.noreply_email == "42+ghost@users.noreply.github.com"


def test_a_missing_email_scope_does_not_fail_the_login() -> None:
    """A token without ``user:email`` gets a 404 from /user/emails; fall through."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/emails":
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json={"id": 9, "login": "solo", "email": None})

    identity = _client(handler).resolve_identity("tok")

    assert identity.email == "9+solo@users.noreply.github.com"


def test_a_401_from_user_raises_an_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    with pytest.raises(GitHubAuthError):
        _client(handler).resolve_identity("expired")


def test_a_transport_failure_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(GitHubUnavailableError):
        _client(handler).resolve_identity("tok")
