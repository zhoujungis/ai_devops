from __future__ import annotations

import pytest
from django.test import Client


def test_healthz_does_not_touch_dependencies() -> None:
    response = Client().get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_readyz_reports_database_and_cache() -> None:
    response = Client().get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["database"]["pgvector"]
    assert body["checks"]["cache"]["ok"] is True


@pytest.mark.django_db
def test_readyz_stays_ok_on_repeated_probes() -> None:
    """The probe renews its sentinel rather than recreating it; both paths report ok."""
    client = Client()

    assert client.get("/readyz").status_code == 200
    assert client.get("/readyz").json()["checks"]["cache"]["ok"] is True


def test_every_response_carries_a_request_id() -> None:
    response = Client().get("/healthz")

    assert response.headers["X-Request-ID"]


def test_inbound_request_id_is_propagated() -> None:
    response = Client().get("/healthz", headers={"X-Request-ID": "abc123"})

    assert response.headers["X-Request-ID"] == "abc123"
