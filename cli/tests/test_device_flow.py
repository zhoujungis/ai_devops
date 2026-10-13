"""The GitHub device-flow polling state machine, driven by mocked HTTP.

These pin the branch that matters and is easy to get wrong: ``authorization_pending``
keeps waiting, ``slow_down`` widens the interval, and the two terminal states stop
with a clear error. Time is injected, so nothing here actually sleeps.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from ai_devops_cli.client import DeviceFlowError, GitHubDeviceFlow


def _flow(handler: Callable[[httpx.Request], httpx.Response]) -> GitHubDeviceFlow:
    return GitHubDeviceFlow(transport=httpx.MockTransport(handler))


def _no_sleep(_seconds: float) -> None:
    return None


def test_request_device_code_returns_the_user_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/device/code"
        body = request.content.decode()
        assert "client_id=Iv1.test" in body
        assert "scope=read%3Auser" in body
        return httpx.Response(
            200,
            json={
                "device_code": "dc",
                "user_code": "WDJB-MJHT",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            },
        )

    code = _flow(handler).request_device_code("Iv1.test", ["read:user"])

    assert code.user_code == "WDJB-MJHT"
    assert code.interval == 5


def test_polling_waits_through_pending_then_returns_the_token() -> None:
    responses = iter(
        [
            {"error": "authorization_pending"},
            {"error": "authorization_pending"},
            {"access_token": "gho_final"},
        ]
    )
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/oauth/access_token"
        return httpx.Response(200, json=next(responses))

    token = _flow(handler).poll_access_token("Iv1.test", "dc", interval=5, sleep=slept.append)

    assert token == "gho_final"
    # It waited twice, both times for the base interval.
    assert slept == [5, 5]


def test_slow_down_widens_the_interval_by_five_seconds() -> None:
    responses = iter([{"error": "slow_down"}, {"access_token": "t"}])
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    token = _flow(handler).poll_access_token("Iv1.test", "dc", interval=5, sleep=slept.append)

    assert token == "t"
    assert slept == [10]


def test_the_device_grant_type_is_sent() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"access_token": "t"})

    _flow(handler).poll_access_token("Iv1.test", "dc", interval=1, sleep=_no_sleep)

    assert "grant_type=urn" in seen["body"]
    assert "device_code=dc" in seen["body"]


def test_an_expired_code_is_a_clear_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "expired_token"})

    with pytest.raises(DeviceFlowError, match="expired"):
        _flow(handler).poll_access_token("Iv1.test", "dc", interval=1, sleep=_no_sleep)


def test_polling_gives_up_once_the_local_deadline_passes() -> None:
    """A server that only ever says ``pending`` must not spin the CLI forever.

    GitHub normally reports the expiry, but the client enforces the budget itself so
    an unhelpful endpoint cannot hang the process.
    """
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "authorization_pending"})

    with pytest.raises(DeviceFlowError, match="expired"):
        _flow(handler).poll_access_token(
            "Iv1.test", "dc", interval=5, expires_in=10, sleep=slept.append
        )

    # It stopped at the budget instead of accumulating sleeps without bound.
    assert slept == [5, 5]


def test_a_token_obtained_before_the_deadline_still_succeeds() -> None:
    responses = iter(
        [
            {"error": "authorization_pending"},
            {"error": "authorization_pending"},
            {"access_token": "just_in_time"},
        ]
    )
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    token = _flow(handler).poll_access_token(
        "Iv1.test", "dc", interval=5, expires_in=15, sleep=slept.append
    )

    assert token == "just_in_time"
    assert slept == [5, 5]


def test_a_denied_authorisation_is_a_clear_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "access_denied"})

    with pytest.raises(DeviceFlowError, match="denied"):
        _flow(handler).poll_access_token("Iv1.test", "dc", interval=1, sleep=_no_sleep)


def test_a_transport_failure_is_reported_not_raised_raw() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(DeviceFlowError):
        _flow(handler).request_device_code("Iv1.test", ["read:user"])
