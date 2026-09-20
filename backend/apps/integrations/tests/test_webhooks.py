"""The webhook endpoint: authenticity, idempotency and push ingestion.

These run with ``transaction=True`` because the view enqueues its task from
``transaction.on_commit`` — inside pytest-django's usual rolled-back transaction
that callback would never fire, and the test would be asserting nothing.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, cast

import pytest
from django.http import HttpResponse
from rest_framework.test import APIClient

from apps.accounts.models import Project
from apps.accounts.tests.factories import OrganizationFactory, ProjectFactory
from apps.codebase.models import Branch, Commit
from apps.integrations.models import GitConnection, WebhookEvent
from apps.integrations.tests.factories import build_repository
from apps.integrations.tests.fakes import FakeGitProvider, make_remote_commit
from apps.integrations.webhooks import compute_signature

pytestmark = pytest.mark.django_db(transaction=True)


def _post(
    connection_pk: Any,
    payload: dict[str, Any],
    *,
    secret: str | None = None,
    delivery_id: str = "delivery-1",
    event: str = "ping",
    signature: str | None = None,
) -> HttpResponse:
    body = json.dumps(payload).encode()
    headers: dict[str, Any] = {"HTTP_X_GITHUB_EVENT": event}
    if delivery_id:
        headers["HTTP_X_GITHUB_DELIVERY"] = delivery_id
    if signature is not None:
        headers["HTTP_X_HUB_SIGNATURE_256"] = signature
    elif secret is not None:
        headers["HTTP_X_HUB_SIGNATURE_256"] = compute_signature(secret, body)

    response = APIClient().post(
        f"/api/v1/webhooks/github/{connection_pk}",
        data=body,
        content_type="application/json",
        **headers,
    )
    return cast(HttpResponse, response)


@pytest.fixture
def connection(db: Any) -> GitConnection:
    project = cast(Project, ProjectFactory(org=OrganizationFactory()))
    return build_repository(project).connection


def test_a_valid_delivery_is_accepted_and_processed(connection: GitConnection) -> None:
    response = _post(
        connection.pk, {"zen": "Design for failure."}, secret=connection.webhook_secret
    )

    assert response.status_code == 202
    event = WebhookEvent.objects.get(delivery_id="delivery-1")
    assert event.event_type == "ping"
    assert event.connection_id == connection.pk
    # Celery runs eagerly in tests, so the on_commit callback has already executed.
    assert event.processed_at is not None


def test_a_redelivered_event_is_ignored(connection: GitConnection) -> None:
    first = _post(connection.pk, {"zen": "one"}, secret=connection.webhook_secret)
    second = _post(connection.pk, {"zen": "one"}, secret=connection.webhook_secret)

    assert first.status_code == 202
    assert second.status_code == 202
    assert json.loads(second.content)["status"] == "duplicate"
    assert WebhookEvent.objects.filter(delivery_id="delivery-1").count() == 1


def test_a_bad_signature_is_rejected_and_nothing_is_stored(connection: GitConnection) -> None:
    response = _post(connection.pk, {"zen": "one"}, signature="sha256=not-the-right-digest")

    assert response.status_code == 401
    assert json.loads(response.content)["error"]["code"] == "invalid_signature"
    assert not WebhookEvent.objects.exists()


def test_a_signature_from_another_secret_is_rejected(connection: GitConnection) -> None:
    response = _post(connection.pk, {"zen": "one"}, secret="a-different-secret")

    assert response.status_code == 401
    assert not WebhookEvent.objects.exists()


def test_a_missing_delivery_id_is_rejected(connection: GitConnection) -> None:
    response = _post(
        connection.pk, {"zen": "one"}, secret=connection.webhook_secret, delivery_id=""
    )

    assert response.status_code == 400
    assert not WebhookEvent.objects.exists()


def test_an_unknown_connection_is_a_404() -> None:
    response = _post(uuid.uuid4(), {"zen": "one"}, secret="irrelevant")

    assert response.status_code == 404


def test_a_push_delivery_ingests_the_pushed_commits(
    connection: GitConnection, monkeypatch: Any
) -> None:
    repository = connection.repositories.get()
    provider = FakeGitProvider(
        commits=[make_remote_commit("pushed-1", minutes_ago=1)],
        full_name=repository.full_name,
    )
    monkeypatch.setattr("apps.integrations.webhooks.provider_for", lambda *a, **k: provider)

    response = _post(
        connection.pk,
        {
            "ref": "refs/heads/main",
            "after": "pushed-1",
            "repository": {"full_name": repository.full_name},
            "commits": [{"id": "pushed-1"}],
        },
        secret=connection.webhook_secret,
        event="push",
    )

    assert response.status_code == 202
    assert Commit.objects.filter(repository=repository, sha="pushed-1").exists()
    assert Branch.objects.get(repository=repository, name="main").head_sha == "pushed-1"


def test_a_push_for_an_untracked_repository_is_processed_without_error(
    connection: GitConnection,
) -> None:
    response = _post(
        connection.pk,
        {
            "ref": "refs/heads/main",
            "after": "abc",
            "repository": {"full_name": "someone/else"},
            "commits": [{"id": "abc"}],
        },
        secret=connection.webhook_secret,
        event="push",
    )

    assert response.status_code == 202
    assert WebhookEvent.objects.get(delivery_id="delivery-1").processed_at is not None


def test_unknown_event_types_are_stored_and_marked_processed(connection: GitConnection) -> None:
    response = _post(
        connection.pk,
        {"action": "opened"},
        secret=connection.webhook_secret,
        event="pull_request",
    )

    assert response.status_code == 202
    event = WebhookEvent.objects.get(delivery_id="delivery-1")
    assert event.event_type == "pull_request"
    assert event.processed_at is not None
