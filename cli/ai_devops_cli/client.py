"""HTTP client for the Copilot API.

It owns the three things no command should have to think about: attaching the
access token, refreshing it once and retrying on a 401, and turning the backend's
error envelope into a typed exception. It also knows how to follow DRF's
pagination, because every list endpoint returns ``{count, next, previous, results}``.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ai_devops_cli.config import Settings, save
from ai_devops_cli.errors import CliError

# GitHub's device-flow endpoints are absolute: they are GitHub's, not ours, so they
# must not be resolved against the configured API base URL.
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DEFAULT_DEVICE_VERIFICATION_URI = "https://github.com/login/device"
CODE_EXPIRED_MESSAGE = "The code expired before it was authorised. Run login again."


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


class DeviceFlowError(CliError):
    """The GitHub device flow could not be completed.

    A subclass of :class:`~ai_devops_cli.errors.CliError` so the command wrapper
    reports it as one clean line instead of a traceback.
    """


@dataclass(frozen=True)
class DeviceCode:
    """What GitHub's ``/login/device/code`` hands back to start polling."""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class GitHubDeviceFlow:
    """Runs the OAuth device grant directly against GitHub.

    Device flow needs no client secret, so the whole thing runs on the user's
    machine. The transport is injectable purely so tests can drive the polling state
    machine — including ``slow_down`` — without waiting in real time.
    """

    def __init__(
        self, *, transport: httpx.BaseTransport | None = None, timeout: float = 30.0
    ) -> None:
        self._http = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "ai-devops-qa-copilot"},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GitHubDeviceFlow:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request_device_code(self, client_id: str, scopes: list[str]) -> DeviceCode:
        payload = self._post(
            GITHUB_DEVICE_CODE_URL, {"client_id": client_id, "scope": " ".join(scopes)}
        )
        if not payload.get("device_code"):
            raise DeviceFlowError(
                str(payload.get("error_description") or "GitHub refused the device code request.")
            )
        return DeviceCode(
            device_code=str(payload["device_code"]),
            user_code=str(payload.get("user_code", "")),
            verification_uri=str(
                payload.get("verification_uri") or DEFAULT_DEVICE_VERIFICATION_URI
            ),
            expires_in=int(payload.get("expires_in", 900)),
            interval=int(payload.get("interval", 5)),
        )

    def poll_access_token(
        self,
        client_id: str,
        device_code: str,
        *,
        interval: int,
        expires_in: int | None = None,
        sleep: Any = time.sleep,
    ) -> str:
        """Poll until the user authorises, then return the access token.

        ``slow_down`` means we are polling too fast, so the interval grows by five
        seconds as GitHub's documentation requires; every other terminal error is
        surfaced as a :class:`DeviceFlowError`.

        ``expires_in`` bounds the total time spent waiting. GitHub normally reports
        the expiry itself (``expired_token``), but relying on that alone lets a
        misbehaving endpoint leave the CLI spinning forever, so the elapsed time is
        accumulated from the injected ``sleep`` and enforced locally. The check sits
        at the *end* of each round, after the poll, so a code that is authorised right
        on the deadline is still exchanged rather than cut off.
        """
        current_interval = interval
        elapsed = 0
        while True:
            payload = self._post(
                GITHUB_ACCESS_TOKEN_URL,
                {
                    "client_id": client_id,
                    "device_code": device_code,
                    "grant_type": DEVICE_GRANT_TYPE,
                },
            )
            if payload.get("access_token"):
                return str(payload["access_token"])

            error = str(payload.get("error") or "")
            if error == "authorization_pending":
                pass
            elif error == "slow_down":
                current_interval += 5
            elif error == "expired_token":
                raise DeviceFlowError(CODE_EXPIRED_MESSAGE)
            elif error == "access_denied":
                raise DeviceFlowError("Authorisation was denied on GitHub.")
            else:
                raise DeviceFlowError(
                    str(payload.get("error_description") or error or "GitHub returned an error.")
                )

            # Stop before waiting again once the budget is spent; the just-completed
            # poll above has already had its chance to return a token.
            if expires_in is not None and elapsed >= expires_in:
                raise DeviceFlowError(CODE_EXPIRED_MESSAGE)
            sleep(current_interval)
            elapsed += current_interval

    def _post(self, url: str, data: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._http.post(url, data=data)
        except httpx.HTTPError as exc:
            raise DeviceFlowError(f"Cannot reach GitHub ({type(exc).__name__}).") from exc
        if response.status_code >= 400:
            raise DeviceFlowError(f"GitHub returned {response.status_code}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise DeviceFlowError("GitHub returned a non-JSON response.") from exc
        return payload


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
    def github_config(self) -> dict[str, Any]:
        """The OAuth app's public parameters, so the app id is never hardcoded here."""
        return self._send("GET", "/auth/github/config", auth=False)

    def login_with_github(self, access_token: str) -> dict[str, Any]:
        """Exchange a GitHub access token for a stored JWT pair."""
        data = self._send("POST", "/auth/github", json={"access_token": access_token}, auth=False)
        user = data.get("user") or {}
        self.settings.access = str(data["access"])
        self.settings.refresh = str(data["refresh"])
        self.settings.github_login = str(user.get("github_login") or "")
        self.settings.email = str(user.get("email") or "")
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
            return self._send(method, path, params=params, json=json, auth=auth, _retried=True)

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
