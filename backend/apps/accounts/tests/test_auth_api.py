from __future__ import annotations

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Membership, User
from apps.accounts.roles import Role
from apps.accounts.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"
ME_URL = "/api/v1/me"

STRONG_PASSWORD = "Str0ng-Passw0rd-42"


def test_register_with_organization_makes_the_creator_admin(api_client: APIClient) -> None:
    response = api_client.post(
        REGISTER_URL,
        {
            "email": "Founder@Example.com",
            "password": STRONG_PASSWORD,
            "display_name": "Founder",
            "organization_name": "Acme",
        },
        format="json",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["access"]
    assert body["refresh"]
    assert body["user"]["email"] == "founder@example.com"

    user = User.objects.get(email="founder@example.com")
    membership = Membership.objects.get(user=user)
    assert membership.org.name == "Acme"
    assert membership.role == Role.ADMIN


def test_register_without_organization_leaves_the_user_unattached(api_client: APIClient) -> None:
    response = api_client.post(
        REGISTER_URL,
        {"email": "solo@example.com", "password": STRONG_PASSWORD},
        format="json",
    )

    assert response.status_code == 201
    assert Membership.objects.filter(user__email="solo@example.com").count() == 0


def test_register_rejects_a_duplicate_email(api_client: APIClient) -> None:
    UserFactory(email="taken@example.com")

    response = api_client.post(
        REGISTER_URL,
        {"email": "taken@example.com", "password": STRONG_PASSWORD},
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert "email" in response.json()["error"]["details"]


def test_register_rejects_a_weak_password(api_client: APIClient) -> None:
    response = api_client.post(
        REGISTER_URL,
        {"email": "weak@example.com", "password": "short"},
        format="json",
    )

    assert response.status_code == 400
    assert "password" in response.json()["error"]["details"]


def test_login_returns_tokens_and_the_user(api_client: APIClient) -> None:
    UserFactory(email="dev@example.com", password=STRONG_PASSWORD)

    response = api_client.post(
        LOGIN_URL,
        {"email": "dev@example.com", "password": STRONG_PASSWORD},
        format="json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access"]
    assert body["refresh"]
    assert body["user"]["email"] == "dev@example.com"


def test_login_email_is_case_insensitive(api_client: APIClient) -> None:
    UserFactory(email="dev@example.com", password=STRONG_PASSWORD)

    response = api_client.post(
        LOGIN_URL,
        {"email": "DEV@Example.COM", "password": STRONG_PASSWORD},
        format="json",
    )

    assert response.status_code == 200


def test_login_rejects_a_wrong_password(api_client: APIClient) -> None:
    UserFactory(email="dev@example.com", password=STRONG_PASSWORD)

    response = api_client.post(
        LOGIN_URL,
        {"email": "dev@example.com", "password": "not-the-password"},
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Invalid email or password."


def test_login_gives_the_same_answer_for_an_unknown_email(api_client: APIClient) -> None:
    response = api_client.post(
        LOGIN_URL,
        {"email": "nobody@example.com", "password": STRONG_PASSWORD},
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Invalid email or password."


def test_inactive_accounts_cannot_log_in(api_client: APIClient) -> None:
    UserFactory(email="gone@example.com", password=STRONG_PASSWORD, is_active=False)

    response = api_client.post(
        LOGIN_URL,
        {"email": "gone@example.com", "password": STRONG_PASSWORD},
        format="json",
    )

    assert response.status_code == 400
    # Same message as an unknown email: the response never reveals account state.
    assert response.json()["error"]["message"] == "Invalid email or password."


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
    UserFactory(email="dev@example.com", password=STRONG_PASSWORD)
    login = api_client.post(
        LOGIN_URL,
        {"email": "dev@example.com", "password": STRONG_PASSWORD},
        format="json",
    ).json()

    response = api_client.post(REFRESH_URL, {"refresh": login["refresh"]}, format="json")

    assert response.status_code == 200
    assert response.json()["access"]


def test_logout_blacklists_the_refresh_token(api_client: APIClient) -> None:
    UserFactory(email="dev@example.com", password=STRONG_PASSWORD)
    login = api_client.post(
        LOGIN_URL,
        {"email": "dev@example.com", "password": STRONG_PASSWORD},
        format="json",
    ).json()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login['access']}")

    logout = client.post(LOGOUT_URL, {"refresh": login["refresh"]}, format="json")

    assert logout.status_code == 204
    retry: Any = api_client.post(REFRESH_URL, {"refresh": login["refresh"]}, format="json")
    assert retry.status_code == 401


def test_logout_rejects_a_garbage_token(auth_client: APIClient) -> None:
    response = auth_client.post(LOGOUT_URL, {"refresh": "not-a-token"}, format="json")

    assert response.status_code == 400
