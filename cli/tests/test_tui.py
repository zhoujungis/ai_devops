"""The dashboard, driven through Textual's test pilot.

Importing the app proves nothing about whether the tables populate or the confirm key
reaches the API; running it under ``run_test`` does.
"""

from __future__ import annotations

from typing import cast

from textual.widgets import DataTable

from ai_devops_cli.client import Client
from ai_devops_cli.tui.app import CopilotApp
from tests.conftest import StubClient

PROJECT = "/orgs/org-1/projects/proj-1"


def _app(stub: StubClient) -> CopilotApp:
    """``StubClient`` duck-types the client, so the cast is the test's claim about it."""
    return CopilotApp(cast(Client, stub))


def _seed(stub: StubClient) -> StubClient:
    stub.responses[f"{PROJECT}/ai/recommendations"] = [
        {
            "id": "r1",
            "type": "create_test_cases",
            "title": "Add cases",
            "risk_level": "low",
            "status": "pending",
        }
    ]
    stub.responses[f"{PROJECT}/ai/findings"] = [
        {
            "id": "f1",
            "severity": "high",
            "agent_code": "code_impact",
            "title": "Risky",
            "confidence": 0.9,
        }
    ]
    stub.responses[f"{PROJECT}/commits"] = [
        {
            "id": "c1",
            "short_sha": "abc123",
            "committed_at": "2026-01-02T03:04:05Z",
            "message": "fix the thing\n\nbody",
            "author_name": "A",
        }
    ]
    stub.responses[f"{PROJECT}/ai/recommendations/r1/confirm"] = {
        "status": "executed",
        "confirmation": None,
    }
    return stub


async def test_every_tab_is_populated(stub: StubClient) -> None:
    app = _app(_seed(stub))

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app.query_one("#proposals", DataTable).row_count == 1
        assert app.query_one("#findings", DataTable).row_count == 1
        assert app.query_one("#commits", DataTable).row_count == 1


async def test_the_commit_row_shows_only_the_subject(stub: StubClient) -> None:
    app = _app(_seed(stub))

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()

        table = app.query_one("#commits", DataTable)
        message = str(table.get_row_at(0)[2])
        assert message == "fix the thing"
        assert "body" not in message


async def test_approving_confirms_first_then_reaches_the_api(stub: StubClient) -> None:
    app = _app(_seed(stub))

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("y")
        await app.workers.wait_for_complete()
        await pilot.pause()

    assert ("post", f"{PROJECT}/ai/recommendations/r1/confirm", {}) in stub.calls


async def test_approving_can_be_cancelled(stub: StubClient) -> None:
    """Approving runs an executor and writes rows, so the prompt must be able to say no."""
    app = _app(_seed(stub))

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

    assert not [call for call in stub.calls if call[0] == "post"]


async def test_rejecting_needs_no_confirmation(stub: StubClient) -> None:
    """Declining writes nothing, so it should not make the user confirm."""
    stub.responses[f"{PROJECT}/ai/recommendations/r1/reject"] = {"status": "rejected"}
    app = _app(_seed(stub))

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("x")
        await app.workers.wait_for_complete()
        await pilot.pause()

    assert ("post", f"{PROJECT}/ai/recommendations/r1/reject", {"reason": ""}) in stub.calls
