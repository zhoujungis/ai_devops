"""GitHub response mapping, driven by mocked HTTP rather than the network.

The provider is the boundary where remote JSON becomes our domain objects, so
these tests pin the mapping — including the fact that the commit endpoint omits
``patch`` for binary files and oversized diffs alike.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from apps.integrations.git.base import (
    GitAuthenticationError,
    GitNotFoundError,
    GitProviderError,
    GitRateLimitError,
)
from apps.integrations.git.github import GitHubProvider

COMMIT_PAYLOAD: dict[str, Any] = {
    "sha": "abc123",
    "commit": {
        "message": "Fix payment retry",
        "author": {"name": "Ada", "email": "ada@example.com", "date": "2026-01-02T03:04:05Z"},
        "committer": {"email": "ada@example.com", "date": "2026-01-02T03:04:05Z"},
    },
    "parents": [{"sha": "parent1"}],
    "stats": {"additions": 12, "deletions": 3},
    "files": [
        {
            "filename": "src/payment/PaymentService.java",
            "status": "added",
            "additions": 12,
            "deletions": 3,
            "patch": "@@ -1 +1 @@",
        },
        {"filename": "assets/logo.png", "status": "modified", "additions": 0, "deletions": 0},
    ],
}


def _provider(
    handler: Any, *, token: str | None = "token", base_url: str = "https://api.github.com"
) -> GitHubProvider:
    return GitHubProvider(token=token, base_url=base_url, transport=httpx.MockTransport(handler))


def test_get_commit_maps_files_and_stats() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/payments/commits/abc123"
        return httpx.Response(200, json=COMMIT_PAYLOAD)

    commit = _provider(handler).get_commit("acme/payments", "abc123")

    assert commit.sha == "abc123"
    assert commit.message == "Fix payment retry"
    assert commit.committed_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert commit.parent_shas == ["parent1"]
    assert commit.additions == 12
    assert commit.deletions == 3
    assert len(commit.files) == 2
    assert commit.files[0].path == "src/payment/PaymentService.java"
    assert commit.files[0].change_type == "add"
    assert commit.files[0].has_patch is True
    # GitHub returned no patch, which happens for binary files *and* oversized diffs.
    assert commit.files[1].has_patch is False


def test_change_types_are_normalised() -> None:
    payload = {
        **COMMIT_PAYLOAD,
        "files": [
            {"filename": "a.py", "status": "removed"},
            {"filename": "b.py", "status": "renamed", "previous_filename": "old/b.py"},
            {"filename": "c.py", "status": "modified"},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    commit = _provider(handler).get_commit("acme/payments", "abc123")

    assert [changed.change_type for changed in commit.files] == ["delete", "rename", "modify"]
    assert commit.files[1].old_path == "old/b.py"


def test_listing_commits_sends_both_date_bounds() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=[])

    _provider(handler).list_commit_refs(
        "acme/payments",
        branch="main",
        since=datetime(2026, 1, 1, tzinfo=UTC),
        until=datetime(2026, 2, 1, tzinfo=UTC),
        per_page=50,
        page=2,
    )

    assert seen["since"] == "2026-01-01T00:00:00Z"
    assert seen["until"] == "2026-02-01T00:00:00Z"
    assert seen["sha"] == "main"
    assert seen["per_page"] == "50"
    assert seen["page"] == "2"


def test_listing_commits_omits_bounds_it_was_not_given() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=[])

    _provider(handler).list_commit_refs("acme/payments", branch="main")

    assert "since" not in seen
    assert "until" not in seen


def test_commit_refs_are_parsed_newest_first() -> None:
    payload = [
        {"sha": "new", "commit": {"committer": {"date": "2026-01-05T00:00:00Z"}}},
        {"sha": "old", "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    refs = _provider(handler).list_commit_refs("acme/payments", branch="main")

    assert [ref.sha for ref in refs] == ["new", "old"]
    assert refs[1].committed_at == datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("status_code", "headers", "expected"),
    [
        (401, {}, GitAuthenticationError),
        (404, {}, GitNotFoundError),
        (403, {"X-RateLimit-Remaining": "0"}, GitRateLimitError),
        (429, {}, GitRateLimitError),
        (500, {}, GitProviderError),
    ],
)
def test_http_failures_map_to_typed_errors(
    status_code: int, headers: dict[str, str], expected: type[Exception]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, headers=headers, json={"message": "nope"})

    with pytest.raises(expected):
        _provider(handler).get_commit("acme/payments", "abc123")


def test_a_forbidden_response_that_is_not_a_rate_limit_stays_generic() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"X-RateLimit-Remaining": "42"}, json={})

    with pytest.raises(GitProviderError) as caught:
        _provider(handler).get_commit("acme/payments", "abc123")

    assert not isinstance(caught.value, GitRateLimitError)


def test_the_token_is_sent_as_a_bearer_header() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "id": 7,
                "full_name": "acme/payments",
                "default_branch": "trunk",
                "private": True,
            },
        )

    repository = _provider(handler).get_repository("acme/payments")

    assert seen["authorization"] == "Bearer token"
    assert repository.external_id == "7"
    assert repository.default_branch == "trunk"
    assert repository.is_private is True


def test_the_rate_limit_header_is_tracked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[], headers={"X-RateLimit-Remaining": "4321"})

    provider = _provider(handler)
    provider.list_commit_refs("acme/payments", branch="main")

    assert provider.rate_limit_remaining == 4321


def test_verify_uses_the_user_endpoint_when_a_token_is_present() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={})

    assert _provider(handler).verify() is True
    assert paths == ["/user"]


def test_verify_falls_back_to_rate_limit_without_a_token() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={})

    assert _provider(handler, token=None).verify() is True
    assert paths == ["/rate_limit"]


def test_verify_reports_failure_instead_of_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    assert _provider(handler).verify() is False


def test_a_self_hosted_base_url_is_honoured() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        return httpx.Response(200, json=[])

    _provider(handler, base_url="https://ghe.internal/api/v3").list_commit_refs(
        "acme/payments", branch="main"
    )

    assert seen["host"] == "ghe.internal"
