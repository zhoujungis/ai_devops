"""GitHub REST implementation of the git provider protocol."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from apps.integrations.git.base import (
    GitAuthenticationError,
    GitNotFoundError,
    GitProviderError,
    GitRateLimitError,
    RemoteBranch,
    RemoteCommit,
    RemoteCommitFile,
    RemoteCommitRef,
    RemoteRepository,
)

DEFAULT_BASE_URL = "https://api.github.com"
API_VERSION = "2022-11-28"

_CHANGE_TYPES: dict[str, str] = {
    "added": "add",
    "removed": "delete",
    "modified": "modify",
    "renamed": "rename",
    "copied": "add",
    "changed": "modify",
}


def parse_timestamp(value: str) -> datetime:
    """Parse GitHub's ISO-8601 timestamps (``...Z``) into aware datetimes."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _as_github_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class GitHubProvider:
    """Thin, explicit client over the endpoints the sync actually needs.

    ``transport`` is injectable so tests can drive the whole sync against recorded
    responses instead of the network.
    """

    provider = "github"

    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "ai-devops-qa-copilot",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._has_token = bool(token)
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )
        self.rate_limit_remaining: int | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubProvider:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------
    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self._client.get(path, params=params)
        self._note_rate_limit(response)

        if response.status_code == 429 or (
            response.status_code == 403 and self.rate_limit_remaining == 0
        ):
            raise GitRateLimitError(
                f"GitHub rate limit reached (remaining={self.rate_limit_remaining})."
            )
        if response.status_code == 401:
            raise GitAuthenticationError("GitHub rejected the credentials.")
        if response.status_code == 404:
            raise GitNotFoundError(f"GitHub returned 404 for {path}.")
        if response.status_code >= 400:
            raise GitProviderError(
                f"GitHub returned {response.status_code} for {path}: {response.text[:200]}"
            )
        return response.json()

    def _note_rate_limit(self, response: httpx.Response) -> None:
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is not None and remaining.isdigit():
            self.rate_limit_remaining = int(remaining)

    # ------------------------------------------------------------------
    # GitProvider
    # ------------------------------------------------------------------
    def verify(self) -> bool:
        try:
            # /user requires a token, so it doubles as a credential check; without
            # one, /rate_limit still proves the host is reachable.
            self._get("/user" if self._has_token else "/rate_limit")
        except GitProviderError:
            return False
        return True

    def get_repository(self, full_name: str) -> RemoteRepository:
        payload = self._get(f"/repos/{full_name}")
        return RemoteRepository(
            external_id=str(payload["id"]),
            full_name=payload["full_name"],
            default_branch=payload.get("default_branch") or "main",
            is_private=bool(payload.get("private", False)),
        )

    def list_branches(
        self, full_name: str, *, per_page: int = 100, page: int = 1
    ) -> list[RemoteBranch]:
        payload = self._get(f"/repos/{full_name}/branches", {"per_page": per_page, "page": page})
        return [
            RemoteBranch(
                name=item["name"],
                sha=item["commit"]["sha"],
                protected=bool(item.get("protected", False)),
            )
            for item in payload
        ]

    def list_commit_refs(
        self,
        full_name: str,
        *,
        branch: str,
        since: datetime | None = None,
        until: datetime | None = None,
        per_page: int = 100,
        page: int = 1,
    ) -> list[RemoteCommitRef]:
        params: dict[str, Any] = {"sha": branch, "per_page": per_page, "page": page}
        if since is not None:
            params["since"] = _as_github_timestamp(since)
        if until is not None:
            params["until"] = _as_github_timestamp(until)
        payload = self._get(f"/repos/{full_name}/commits", params)
        return [
            RemoteCommitRef(
                sha=item["sha"],
                committed_at=parse_timestamp(item["commit"]["committer"]["date"]),
            )
            for item in payload
        ]

    def get_commit(self, full_name: str, sha: str) -> RemoteCommit:
        payload = self._get(f"/repos/{full_name}/commits/{sha}")
        commit = payload["commit"]
        author = commit.get("author") or {}
        committer = commit.get("committer") or {}
        stats = payload.get("stats") or {}

        committed_at = committer.get("date") or author.get("date")
        if not committed_at:
            raise GitProviderError(f"Commit {payload.get('sha')} has no committer date.")

        return RemoteCommit(
            sha=payload["sha"],
            message=commit.get("message", ""),
            committed_at=parse_timestamp(committed_at),
            author_name=author.get("name", ""),
            author_email=author.get("email", ""),
            committer_email=committer.get("email", ""),
            parent_shas=[parent["sha"] for parent in payload.get("parents", [])],
            additions=stats.get("additions", 0),
            deletions=stats.get("deletions", 0),
            files=[_to_file(item) for item in payload.get("files", [])],
        )


def _to_file(payload: dict[str, Any]) -> RemoteCommitFile:
    patch = payload.get("patch")
    return RemoteCommitFile(
        path=payload["filename"],
        old_path=payload.get("previous_filename"),
        change_type=_CHANGE_TYPES.get(payload.get("status", "modified"), "modify"),
        additions=payload.get("additions", 0),
        deletions=payload.get("deletions", 0),
        patch=patch,
        has_patch=patch is not None,
    )
