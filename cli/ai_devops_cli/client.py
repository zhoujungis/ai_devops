"""HTTP client for the Copilot API.

It owns the three things no command should have to think about: attaching the
access token, refreshing it once and retrying on a 401, and turning the backend's
error envelope into a typed exception. It also knows how to follow DRF's
pagination, because every list endpoint returns ``{count, next, previous, results}``.
"""

from __future__ import annotations

import contextlib
from typing import Any

import httpx

from ai_devops_cli.config import Settings, save


class ApiError(RuntimeError):
    """A non-2xx response, carrying the backend's ``{"error": {...}}`` envelope."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: Any = None,
        request_id: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details if details is not None else {}
        self.request_id = request_id


class Client:
    """A thin, synchronous wrapper. One instance per command invocation."""

    def __init__(
        self,
        settings: Settings,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        # `transport` is a test seam: passing an httpx.MockTransport exercises the
        # auth, pagination and error-handling logic without a live server.
        self._http = httpx.Client(base_url=settings.base_url, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- auth ---------------------------------------------------------------
    def login(self, email: str, password: str) -> dict[str, Any]:
        data = self._send(
            "POST", "/auth/login", json={"email": email, "password": password}, auth=False
        )
        self.settings.access = str(data["access"])
        self.settings.refresh = str(data["refresh"])
        self.settings.email = str(data["user"]["email"])
        save(self.settings)
        return data

    def refresh_access(self) -> bool:
        """Swap the refresh token for a new access token. False when it cannot."""
        if not self.settings.refresh:
            return False
        try:
            data = self._send(
                "POST", "/auth/refresh", json={"refresh": self.settings.refresh}, auth=False
            )
        except ApiError:
            self.settings.clear_tokens()
            save(self.settings)
            return False
        self.settings.access = str(data["access"])
        save(self.settings)
        return True

    def logout(self) -> None:
        if self.settings.refresh:
            # A logout that fails server-side still clears the local session.
            with contextlib.suppress(ApiError):
                self._send("POST", "/auth/logout", json={"refresh": self.settings.refresh})
        self.settings.clear_tokens()
        save(self.settings)

    # -- verbs --------------------------------------------------------------
    def get(self, path: str, **params: Any) -> Any:
        return self._send("GET", path, params=params or None)

    def post(self, path: str, json: Any = None) -> Any:
        return self._send("POST", path, json=json)

    def patch(self, path: str, json: Any = None) -> Any:
        return self._send("PATCH", path, json=json)

    def delete(self, path: str) -> Any:
        return self._send("DELETE", path)

    # -- lists --------------------------------------------------------------
    def results(self, path: str, *, limit: int | None = None, **params: Any) -> list[Any]:
        """Rows of a paginated endpoint, following ``next`` up to ``limit``."""
        rows: list[Any] = []
        query: dict[str, Any] | None = dict(params) or None
        url: str | None = path
        while url is not None:
            data = self._send("GET", url, params=query)
            if isinstance(data, dict) and "results" in data:
                rows.extend(data["results"])
                url = data.get("next")
                # `next` already carries the query string; re-sending it would
                # duplicate the filters.
                query = None
            else:
                rows.extend(data if isinstance(data, list) else [data])
                url = None
            if limit is not None and len(rows) >= limit:
                return rows[:limit]
        return rows

    # -- transport ----------------------------------------------------------
    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        auth: bool = True,
        _retried: bool = False,
    ) -> Any:
        headers: dict[str, str] = {}
        if auth and self.settings.access:
            headers["Authorization"] = f"Bearer {self.settings.access}"

        try:
            response = self._http.request(method, path, params=params, json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise ApiError(
                0,
                "connection_error",
                f"Cannot reach {self.settings.base_url} ({type(exc).__name__}). "
                "Is the backend running?",
            ) from exc

        if response.status_code == 401 and auth and not _retried and self.refresh_access():
            return self._send(
                method, path, params=params, json=json, auth=auth, _retried=True
            )

        if response.status_code >= 400:
            raise self._error(response)

        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    @staticmethod
    def _error(response: httpx.Response) -> ApiError:
        try:
            payload = response.json()
        except ValueError:
            payload = None

        envelope = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(envelope, dict):
            return ApiError(
                response.status_code,
                str(envelope.get("code", "error")),
                str(envelope.get("message", "Request failed")),
                envelope.get("details", {}),
                str(envelope.get("request_id", "")),
            )
        return ApiError(
            response.status_code,
            f"http_{response.status_code}",
            (response.text or "Request failed")[:300],
        )
