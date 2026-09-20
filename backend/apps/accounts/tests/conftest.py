from __future__ import annotations

from typing import Any, cast

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.tests.factories import DEFAULT_PASSWORD, UserFactory


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def user(db: Any) -> User:
    return cast(User, UserFactory())


@pytest.fixture
def auth_client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


__all__ = ["DEFAULT_PASSWORD", "api_client", "auth_client", "user"]
