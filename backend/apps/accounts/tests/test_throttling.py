"""Rate limits on the unauthenticated entry points.

``config.settings.test`` sets these rates effectively unlimited so the rest of the
suite is unaffected. The rates are lowered here instead, by patching the class
attribute the throttle actually reads — ``override_settings`` on ``REST_FRAMEWORK``
does *not* reach it, because ``SimpleRateThrottle.THROTTLE_RATES`` is bound to the
settings dict at import time. An unthrottled login is a password oracle and an
unthrottled webhook is an unauthenticated write path, so this is worth asserting.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

pytestmark = pytest.mark.django_db


def _rates(monkeypatch: pytest.MonkeyPatch, **rates: str) -> None:
    monkeypatch.setattr(SimpleRateThrottle, "THROTTLE_RATES", rates)


@pytest.fixture(autouse=True)
def _clear_throttle_cache() -> Iterator[None]:
    # The throttle counters live in the cache, which the DB rollback does not reset.
    cache.clear()
    yield
    cache.clear()


def test_login_is_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    _rates(monkeypatch, login="2/min")
    client = APIClient()
    payload = {"email": "nobody@example.com", "password": "not-the-password"}

    first = client.post("/api/v1/auth/login", payload, format="json")
    second = client.post("/api/v1/auth/login", payload, format="json")
    third = client.post("/api/v1/auth/login", payload, format="json")

    assert first.status_code != 429
    assert second.status_code != 429
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "throttled"


def test_refresh_is_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    _rates(monkeypatch, refresh="2/min")
    client = APIClient()
    payload = {"refresh": "not-a-real-token"}

    client.post("/api/v1/auth/refresh", payload, format="json")
    client.post("/api/v1/auth/refresh", payload, format="json")
    third = client.post("/api/v1/auth/refresh", payload, format="json")

    assert third.status_code == 429


def test_the_webhook_is_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    _rates(monkeypatch, webhook="2/min")
    client = APIClient()
    url = f"/api/v1/webhooks/github/{uuid.uuid4()}"

    first = client.post(url, data=b"{}", content_type="application/json")
    client.post(url, data=b"{}", content_type="application/json")
    third = client.post(url, data=b"{}", content_type="application/json")

    assert first.status_code != 429
    assert third.status_code == 429


def test_an_unscoped_endpoint_is_not_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The throttles are scoped; ordinary reads must not be caught by them."""
    _rates(monkeypatch, login="2/min", register="2/min", refresh="2/min", webhook="2/min")
    client = APIClient()

    for _ in range(5):
        assert client.get("/healthz").status_code == 200
