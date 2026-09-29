"""Shared fixtures for the CLI tests.

Two seams make the whole command layer testable without a server: the config file
(redirected to a temp path through ``COPILOT_CONFIG``) and the HTTP client (replaced
with a recording stub). Everything above them — argument parsing, scope resolution,
rendering, error handling — is exercised for real.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ai_devops_cli import context


class StubClient:
    """Records every call and replays canned responses keyed by path.

    A response value that is an ``Exception`` is raised instead of returned, which is
    how the error paths are exercised without a live API.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.responses: dict[str, Any] = {}

    def _lookup(self, path: str) -> Any:
        value = self.responses.get(path, [])
        if isinstance(value, Exception):
            raise value
        return value

    def results(self, path: str, *, limit: int | None = None, **params: Any) -> list[Any]:
        self.calls.append(("results", path, params))
        rows = list(self._lookup(path))
        return rows[:limit] if limit is not None else rows

    def get(self, path: str, **params: Any) -> Any:
        self.calls.append(("get", path, params))
        value = self._lookup(path)
        return value if value != [] else {}

    def post(self, path: str, json: Any = None) -> Any:
        self.calls.append(("post", path, json))
        value = self._lookup(path)
        return value if value != [] else {}

    def patch(self, path: str, json: Any = None) -> Any:
        self.calls.append(("patch", path, json))
        value = self._lookup(path)
        return value if value != [] else {}

    def delete(self, path: str) -> Any:
        self.calls.append(("delete", path, None))
        return None

    def login(self, email: str, password: str) -> dict[str, Any]:
        self.calls.append(("login", email, password))
        return {"user": {"email": email}, "access": "a", "refresh": "r"}

    def logout(self) -> None:
        self.calls.append(("logout", "", None))


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A logged-in config with a project selected, at a throwaway path."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "base_url": "http://api.test/api/v1",
                "access": "access-token",
                "refresh": "refresh-token",
                "email": "me@example.com",
                "org_id": "org-1",
                "org_name": "demo-org",
                "project_id": "proj-1",
                "project_name": "qa",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COPILOT_CONFIG", str(path))
    # Drop settings/client cached by a previous test.
    context.configure()
    return path


@pytest.fixture
def stub(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> StubClient:
    client = StubClient()
    monkeypatch.setattr(context, "client", lambda: client)
    return client
