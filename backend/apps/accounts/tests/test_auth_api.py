from __future__ import annotations

from typing import Any, cast

import pytest
from rest_framework.test import APIClient

from apps.accounts import github
from apps.accounts.models import User
from apps.accounts.services import issue_tokens
from apps.accounts.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

GITHUB_URL = "/api/v1/auth/github"
GITHUB_CONFIG_URL = "/api/v1/auth/github/config"
REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"
ME_URL = "/api/v1/me"

STRONG_PASSWORD = "Str0ng-Passw0rd-42"


def _identity(
    github_id: int = 1001,
    login: str = "octocat",
    email: str = "octocat@example.com",
    avatar_url: str = "https://avatars.example/u/1001",
) -> github.GitHubIdentity:
    return github.GitHubIdentity(
        github_id=github_id, login=login, email=email, avatar_url=avatar_url
    )


def _github_returns(monkeypatch: pytest.MonkeyPatch, identity: github.GitHubIdentity) -> None:
    monkeypatch.setattr(github, "identity_from_token", lambda _token: identity)


def _github_raises(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def _boom(_token: str) -> github.GitHubIdentity:
        raise exc

    monkeypatch.setattr(github, "identity_from_token", _boom)


# ---------------------------------------------------------------------------
# The email + password endpoints are gone
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/api/v1/auth/login", "/api/v1/auth/register"])
def test_the_email_password_endpoints_are_gone(api_client: APIClient, path: str) -> None:
    assert api_client.post(path, {}, format="json").status_code == 404


# ---------------------------------------------------------------------------
# The GitHub login exchange
# ---------------------------------------------------------------------------
def test_github_login_returns_tokens_and_the_user(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _github_returns(monkeypatch, _identity())

    response = api_client.post(GITHUB_URL, {"access_token": "gho_token"}, format="json")

    assert response.status_code == 200
    body = response.json()
    assert body["access"]
    assert body["refresh"]
    assert body["user"]["github_login"] == "octocat"
    assert body["user"]["email"] == "octocat@example.com"


def test_github_login_creates_a_new_user(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _github_returns(monkeypatch, _identity(email="Octocat@Example.com"))

    response = api_client.post(GITHUB_URL, {"access_token": "gho_token"}, format="json")

    assert response.status_code == 200
    user = User.objects.get(github_id=1001)
    # Emails are still stored lowercased, and the account is deliberately passwordless.
    assert user.email == "octocat@example.com"
    assert user.has_usable_password() is False
    assert user.github_login == "octocat"


def test_github_login_matches_an_existing_user_by_github_id(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = User.objects.create_from_github(
        github_id=1001, login="octocat", email="octocat@example.com", avatar_url=""
    )
    # The user renamed themselves on GitHub; identity is the id, not the login.
    _github_returns(monkeypatch, _identity(login="octocat-renamed"))

    response = api_client.post(GITHUB_URL, {"access_token": "gho_token"}, format="json")

    assert response.status_code == 200
    assert response.json()["user"]["id"] == str(existing.id)
    assert User.objects.filter(github_id=1001).count() == 1
    existing.refresh_from_db()
    assert existing.github_login == "octocat-renamed"


def test_github_login_rejects_an_invalid_token(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _github_raises(monkeypatch, github.GitHubAuthError("bad token"))

    response = api_client.post(GITHUB_URL, {"access_token": "garbage"}, format="json")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_github_token"


def test_github_login_reports_an_upstream_outage_as_503(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _github_raises(monkeypatch, github.GitHubUnavailableError("github down"))

    response = api_client.post(GITHUB_URL, {"access_token": "gho_token"}, format="json")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "github_unavailable"


def test_inactive_accounts_cannot_log_in(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    User.objects.create_from_github(
        github_id=1001,
        login="octocat",
        email="octocat@example.com",
        avatar_url="",
    )
    User.objects.filter(github_id=1001).update(is_active=False)
    _github_returns(monkeypatch, _identity())

    response = api_client.post(GITHUB_URL, {"access_token": "gho_token"}, format="json")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


def test_github_login_falls_back_to_a_noreply_email_on_a_collision(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old email-identified account must not make the GitHub login blow up."""
    UserFactory(email="octocat@example.com")
    _github_returns(monkeypatch, _identity())

    response = api_client.post(GITHUB_URL, {"access_token": "gho_token"}, format="json")

    assert response.status_code == 200
    created = User.objects.get(github_id=1001)
    assert created.email == "1001+octocat@users.noreply.github.com"


def test_a_concurrent_first_login_adopts_the_row_the_other_request_created(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two simultaneous first logins must not turn the loser into a 500.

    The race is forced directly: the first ``filter(github_id=...)`` is made to miss
    even though the row exists, so the view then tries to insert a duplicate and hits
    the unique constraint — exactly what the real race produces.
    """
    existing = User.objects.create_from_github(
        github_id=1001, login="octocat", email="octocat@example.com", avatar_url=""
    )
    real_filter = User.objects.filter
    github_id_lookups = {"count": 0}

    def filter_missing_once(*args: Any, **kwargs: Any) -> Any:
        if "github_id" in kwargs and github_id_lookups["count"] == 0:
            github_id_lookups["count"] += 1
            return User.objects.none()
        return real_filter(*args, **kwargs)

    monkeypatch.setattr(User.objects, "filter", filter_missing_once)
    _github_returns(monkeypatch, _identity())

    response = api_client.post(GITHUB_URL, {"access_token": "gho_token"}, format="json")

    assert response.status_code == 200
    assert response.json()["user"]["id"] == str(existing.id)
    assert User.objects.filter(github_id=1001).count() == 1


def test_github_config_returns_the_client_id_and_scopes(
    api_client: APIClient, settings: Any
) -> None:
    settings.GITHUB_CLIENT_ID = "Iv1.test-client"
    settings.GITHUB_OAUTH_SCOPES = ["read:user", "user:email"]

    response = api_client.get(GITHUB_CONFIG_URL)

    assert response.status_code == 200
    assert response.json() == {
        "client_id": "Iv1.test-client",
        "scopes": ["read:user", "user:email"],
    }


# ---------------------------------------------------------------------------
# Unchanged surface
# ---------------------------------------------------------------------------
def test_me_requires_authentication(api_client: APIClient) -> None:
    response = api_client.get(ME_URL)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_me_returns_the_authenticated_user(auth_client: APIClient, user: User) -> None:
    response = auth_client.get(ME_URL)

    assert response.status_code == 200
    assert response.json()["email"] == user.email


def test_me_email_is_read_only(auth_client: APIClient, user: User) -> None:
    response = auth_client.patch(ME_URL, {"email": "hijack@example.com"}, format="json")

    assert response.status_code == 200
    assert response.json()["email"] == user.email

    user.refresh_from_db()
    assert user.email != "hijack@example.com"


def test_me_can_update_the_display_name(auth_client: APIClient) -> None:
    response = auth_client.patch(ME_URL, {"display_name": "Renamed"}, format="json")

    assert response.status_code == 200
    assert response.json()["display_name"] == "Renamed"


def test_refresh_exchanges_a_refresh_token_for_an_access_token(api_client: APIClient) -> None:
    user = cast(User, UserFactory(email="dev@example.com", password=STRONG_PASSWORD))
    tokens = issue_tokens(user)

    response = api_client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")

    assert response.status_code == 200
    assert response.json()["access"]


def test_logout_blacklists_the_refresh_token(api_client: APIClient) -> None:
    user = cast(User, UserFactory(email="dev@example.com", password=STRONG_PASSWORD))
    tokens = issue_tokens(user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    logout = client.post(LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json")

    assert logout.status_code == 204
    retry: Any = api_client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
    assert retry.status_code == 401


def test_logout_rejects_a_garbage_token(auth_client: APIClient) -> None:
    response = auth_client.post(LOGOUT_URL, {"refresh": "not-a-token"}, format="json")

    assert response.status_code == 400
