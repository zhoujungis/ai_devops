"""The HTTP client: auth, refresh-on-401, error envelope and pagination.

These use ``httpx.MockTransport`` so the real request path runs — headers, retry,
envelope parsing — without a server.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from ai_devops_cli.client import ApiError, Client
from ai_devops_cli.config import Settings


@pytest.fixture
def settings(config_file: Path) -> Settings:
    # `config_file` points AIDEVOPS_CONFIG at a temp file, so `save()` never touches
    # the developer's real config.
    return Settings(base_url="http://api.test/api/v1")


def _client(settings: Settings, handler: Callable[[httpx.Request], httpx.Response]) -> Client:
    return Client(settings, transport=httpx.MockTransport(handler))


def test_login_with_github_stores_the_token_pair_and_the_login(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/auth/github"
        return httpx.Response(
            200,
            json={
                "access": "A",
                "refresh": "R",
                "user": {"github_login": "octocat", "email": "octocat@example.com"},
            },
        )

    _client(settings, handler).login_with_github("gho_token")

    assert settings.access == "A"
    assert settings.refresh == "R"
    assert settings.github_login == "octocat"
    assert settings.email == "octocat@example.com"


def test_github_config_reads_the_app_parameters(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/auth/github/config"
        return httpx.Response(200, json={"client_id": "Iv1.x", "scopes": ["read:user"]})

    config = _client(settings, handler).github_config()

    assert config == {"client_id": "Iv1.x", "scopes": ["read:user"]}


def test_a_401_refreshes_once_and_retries(settings: Settings) -> None:
    settings.access = "old"
    settings.refresh = "refresh-token"
    seen = {"refreshed": 0, "reads": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/refresh"):
            seen["refreshed"] += 1
            return httpx.Response(200, json={"access": "new"})
        seen["reads"] += 1
        if request.headers.get("Authorization") == "Bearer old":
            return httpx.Response(
                401, json={"error": {"code": "not_authenticated", "message": "expired"}}
            )
        return httpx.Response(200, json={"ok": True})

    assert _client(settings, handler).get("/me") == {"ok": True}
    assert seen["refreshed"] == 1
    assert seen["reads"] == 2, "the original request should have been retried once"
    assert settings.access == "new"


def test_an_error_envelope_becomes_a_typed_error(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "error": {
                    "code": "cannot_execute",
                    "message": "No executor named 'x'.",
                    "details": {"available": ["create_test_cases"]},
                    "request_id": "req-9",
                }
            },
        )

    with pytest.raises(ApiError) as caught:
        _client(settings, handler).post("/x")

    assert caught.value.status == 409
    assert caught.value.code == "cannot_execute"
    assert caught.value.request_id == "req-9"
    assert caught.value.details == {"available": ["create_test_cases"]}


def test_results_follows_next_until_exhausted(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            return httpx.Response(
                200, json={"count": 2, "next": None, "previous": None, "results": [{"id": 2}]}
            )
        return httpx.Response(
            200,
            json={
                "count": 2,
                "next": "http://api.test/api/v1/items?page=2",
                "previous": None,
                "results": [{"id": 1}],
            },
        )

    assert _client(settings, handler).results("/items") == [{"id": 1}, {"id": 2}]


def test_results_stops_at_the_limit(settings: Settings) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={
                "count": 5,
                "next": "http://api.test/api/v1/items?page=2",
                "results": [{"id": 1}, {"id": 2}, {"id": 3}],
            },
        )

    assert _client(settings, handler).results("/items", limit=2) == [{"id": 1}, {"id": 2}]
    assert calls["n"] == 1, "the second page must not be fetched once the limit is met"


def test_a_transport_error_is_reported_as_a_connection_error(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ApiError) as caught:
        _client(settings, handler).get("/x")

    assert caught.value.code == "connection_error"
    assert caught.value.status == 0


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:8000/api/v1",
        "http://localhost:5173/api/v1",
        "http://[::1]:8000/api/v1",
    ],
)
def test_a_local_api_ignores_the_environment_proxy(config_file: Path, base_url: str) -> None:
    """HTTP_PROXY exists to reach the outside world, and the local API is not outside.

    Without this, a developer who has one set -- routine behind a corporate proxy --
    sends their 127.0.0.1 requests to the proxy, which answers 404 for a path it has
    never heard of.
    """
    with Client(Settings(base_url=base_url)) as client:
        assert client._http.trust_env is False


def test_a_remote_api_still_honours_the_environment_proxy(config_file: Path) -> None:
    """A proxy may be the only way out to a hosted API, so leave it in play there."""
    with Client(Settings(base_url="https://qa.example.com/api/v1")) as client:
        assert client._http.trust_env is True
