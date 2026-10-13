"""The command layer: rendering, scope resolution and error exit codes.

Driven through Typer's ``CliRunner`` so argument parsing and command registration are
covered too — calling the functions directly would skip exactly the wiring that tends
to break.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from ai_devops_cli import context
from ai_devops_cli.client import ApiError
from ai_devops_cli.commands import auth, repos
from ai_devops_cli.main import app
from tests.conftest import StubClient

runner = CliRunner()

PROJECT = "/orgs/org-1/projects/proj-1"


def _text(result: Any) -> str:
    """stdout plus stderr, whichever stream Click kept them on."""
    return result.output + (getattr(result, "stderr", "") or "")


def test_projects_marks_the_selected_one(stub: StubClient) -> None:
    stub.responses["/orgs"] = [
        {"id": "org-1", "slug": "demo-org", "name": "Demo", "plan": "free", "role": "admin"}
    ]
    stub.responses["/orgs/org-1/projects"] = [
        {"id": "proj-1", "slug": "qa", "name": "QA", "role": "admin"},
        {"id": "proj-2", "slug": "pay", "name": "Pay", "role": "admin"},
    ]

    result = runner.invoke(app, ["projects"])

    assert result.exit_code == 0
    assert "qa" in result.output
    assert "pay" in result.output
    assert "*" in result.output, "the current project should be marked"


def test_json_output_is_machine_readable(stub: StubClient) -> None:
    stub.responses["/orgs"] = [
        {"id": "org-1", "slug": "demo-org", "name": "Demo", "plan": "free", "role": "admin"}
    ]
    stub.responses["/orgs/org-1/projects"] = [
        {"id": "proj-1", "slug": "qa", "name": "QA", "role": "admin"}
    ]

    result = runner.invoke(app, ["--json", "projects"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload[0]["slug"] == "qa"
    assert payload[0]["org_slug"] == "demo-org"


def test_use_persists_the_selected_project(stub: StubClient, config_file: Path) -> None:
    stub.responses["/orgs"] = [
        {"id": "org-1", "slug": "demo-org", "name": "Demo", "plan": "free", "role": "admin"}
    ]
    stub.responses["/orgs/org-1/projects"] = [
        {"id": "proj-2", "slug": "pay", "name": "Pay", "role": "admin"}
    ]

    result = runner.invoke(app, ["use", "pay"])

    assert result.exit_code == 0
    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["project_id"] == "proj-2"
    assert saved["org_id"] == "org-1"


def test_an_unknown_project_is_a_clean_error(stub: StubClient) -> None:
    stub.responses["/orgs"] = []

    result = runner.invoke(app, ["use", "ghost"])

    assert result.exit_code == 1
    assert "No project matching" in _text(result)


def test_a_missing_project_scope_is_reported_not_crashed(stub: StubClient) -> None:
    stub.responses["/orgs"] = []
    runner_result = runner.invoke(app, ["findings"])

    assert runner_result.exit_code == 0
    # The config has a project, so this should actually call the API; assert the call
    # went to the scoped path rather than to a bare /findings.
    assert any(path == f"{PROJECT}/ai/findings" for _, path, _ in stub.calls)


def test_approve_surfaces_the_server_error(stub: StubClient) -> None:
    stub.responses[f"{PROJECT}/ai/recommendations/r1/confirm"] = ApiError(
        409, "cannot_execute", "No executor named 'add_guard_test'.", {}, "req-1"
    )

    result = runner.invoke(app, ["approve", "r1"])

    assert result.exit_code == 1
    text = _text(result)
    assert "No executor named" in text
    assert "cannot_execute" in text


def test_explain_renders_the_chain_and_its_gaps(stub: StubClient) -> None:
    stub.responses[f"{PROJECT}/commits"] = [
        {
            "id": "c1",
            "sha": "abcdef123456",
            "short_sha": "abcdef123456",
            "message": "fix the thing",
            "author_name": "A",
            "committed_at": "2026-01-01T00:00:00Z",
            "additions": 1,
            "deletions": 1,
        }
    ]
    stub.responses[f"{PROJECT}/commits/c1/explain"] = {
        "commit": {
            "id": "c1",
            "repository": "r1",
            "sha": "abcdef123456",
            "short_sha": "abcdef123456",
            "message": "fix the thing",
            "committed_at": "2026-01-01T00:00:00Z",
        },
        "modules": [],
        "regression_candidates": [],
        "historical_bugs": [],
        "requirement": None,
        "requirement_source": "none",
        "releases": [],
        "data_gaps": ["No modules resolved for this commit."],
    }

    result = runner.invoke(app, ["explain", "abcdef"])

    assert result.exit_code == 0
    assert "No modules resolved for this commit." in _text(result)


def test_analyse_without_wait_returns_immediately(stub: StubClient) -> None:
    stub.responses[f"{PROJECT}/ai/analyses"] = {"id": "job-1", "status": "queued"}

    result = runner.invoke(app, ["analyse", "-a", "code_impact", "--target-id", "abc"])

    assert result.exit_code == 0
    assert "job-1" in result.output
    assert (
        "post",
        f"{PROJECT}/ai/analyses",
        {"agent": "code_impact", "params": {}, "target_id": "abc"},
    ) in stub.calls


def test_analyse_wait_reports_success(stub: StubClient) -> None:
    stub.responses[f"{PROJECT}/ai/analyses"] = {"id": "job-1", "status": "queued"}
    stub.responses[f"{PROJECT}/ai/jobs/job-1"] = {
        "id": "job-1",
        "status": "succeeded",
        "findings": ["f1"],
        "usage": {"input_tokens": 12},
    }

    result = runner.invoke(app, ["analyse", "-a", "code_impact", "--wait", "--interval", "0"])

    assert result.exit_code == 0
    assert "succeeded" in result.output


def test_analyse_wait_fails_loudly_when_the_job_fails(stub: StubClient) -> None:
    """--wait is for scripts, so a failed analysis must not exit 0."""
    stub.responses[f"{PROJECT}/ai/analyses"] = {"id": "job-1", "status": "queued"}
    stub.responses[f"{PROJECT}/ai/jobs/job-1"] = {
        "id": "job-1",
        "status": "failed",
        "error": "provider exploded",
    }

    result = runner.invoke(app, ["analyse", "-a", "code_impact", "--wait", "--interval", "0"])

    assert result.exit_code == 1
    assert "provider exploded" in _text(result)


def test_analyse_wait_json_output_stays_parseable(stub: StubClient) -> None:
    """--json is for scripts, so the progress spinner must not land inside the JSON."""
    stub.responses[f"{PROJECT}/ai/analyses"] = {"id": "job-1", "status": "queued"}
    stub.responses[f"{PROJECT}/ai/jobs/job-1"] = {
        "id": "job-1",
        "status": "succeeded",
        "findings": [],
        "usage": {"input_tokens": 1},
    }

    result = runner.invoke(
        app, ["--json", "analyse", "-a", "code_impact", "--wait", "--interval", "0"]
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["status"] == "succeeded"


def test_status_confirms_the_session_with_the_server(stub: StubClient) -> None:
    stub.responses["/me"] = {"email": "me@example.com"}

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "ok (me@example.com)" in result.output


def test_status_reports_a_token_the_server_has_rejected(stub: StubClient) -> None:
    stub.responses["/me"] = ApiError(401, "not_authenticated", "expired", {}, "req-1")

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "rejected (not_authenticated)" in result.output


def test_trace_renders_the_runs_and_tool_calls(stub: StubClient) -> None:
    stub.responses[f"{PROJECT}/ai/jobs/job-1/trace"] = [
        {
            "sequence": 1,
            "model": "gpt-4o",
            "prompt_id": "code_impact",
            "prompt_version": 3,
            "input_tokens": 100,
            "output_tokens": 20,
            "latency_ms": 900,
            "status": "succeeded",
            "tool_calls": [
                {
                    "sequence": 1,
                    "tool_name": "get_commit",
                    "status": "ok",
                    "scope_denied": False,
                    "duration_ms": 12,
                }
            ],
        }
    ]

    result = runner.invoke(app, ["trace", "job-1"])

    assert result.exit_code == 0
    assert "gpt-4o" in result.output
    assert "code_impact" in result.output
    assert "get_commit" in result.output


def test_trace_surfaces_a_missing_job(stub: StubClient) -> None:
    stub.responses[f"{PROJECT}/ai/jobs/nope/trace"] = ApiError(
        404, "not_found", "No such job.", {}, "req-2"
    )

    result = runner.invoke(app, ["trace", "nope"])

    assert result.exit_code == 1
    assert "No such job." in _text(result)


def test_providers_marks_the_default_endpoint(stub: StubClient) -> None:
    stub.responses["/orgs/org-1/ai-providers"] = [
        {
            "id": "p1",
            "label": "Main",
            "provider_type": "openai",
            "is_default": True,
            "has_api_key": True,
            "status": "active",
        }
    ]

    result = runner.invoke(app, ["providers"])

    assert result.exit_code == 0
    assert "Main" in result.output
    assert "*" in result.output


def test_provider_add_builds_the_capability_map_and_keeps_the_key_quiet(
    stub: StubClient,
) -> None:
    stub.responses["/orgs/org-1/ai-providers"] = {"id": "p9", "label": "Main"}

    result = runner.invoke(
        app,
        [
            "provider-add",
            "-l",
            "Main",
            "--type",
            "openai",
            "--model",
            "chat=gpt-4o",
            "--model",
            "embedding=text-embedding-3-large",
            "--api-key",
            "sk-secret",
            "--default",
        ],
    )

    assert result.exit_code == 0
    call = next(c for c in stub.calls if c[0] == "post")
    assert call[1] == "/orgs/org-1/ai-providers"
    body = call[2]
    assert body["capability_models"] == {
        "chat": "gpt-4o",
        "embedding": "text-embedding-3-large",
    }
    assert body["is_default"] is True
    assert body["api_key"] == "sk-secret"
    assert "sk-secret" not in result.output, "the key must never be echoed back"


def test_provider_add_rejects_a_malformed_model_flag(stub: StubClient) -> None:
    result = runner.invoke(
        app, ["provider-add", "-l", "Main", "--model", "justamodel", "--api-key", "k"]
    )

    assert result.exit_code == 1
    assert "capability=model" in _text(result)


def test_provider_verify_reports_the_verdict(stub: StubClient) -> None:
    """A key the endpoint refused should be visible here, not as a failed analysis."""
    stub.responses["/orgs/org-1/ai-providers/p1/verify"] = {
        "verified": False,
        "status": "invalid",
        "checked_at": "2026-01-02T03:04:05Z",
    }

    result = runner.invoke(app, ["provider-verify", "p1"])

    assert result.exit_code == 0
    assert "invalid" in _text(result)
    assert ("post", "/orgs/org-1/ai-providers/p1/verify", None) in stub.calls


def test_provider_verify_reports_a_working_key(stub: StubClient) -> None:
    stub.responses["/orgs/org-1/ai-providers/p1/verify"] = {
        "verified": True,
        "status": "active",
        "checked_at": "2026-01-02T03:04:05Z",
    }

    result = runner.invoke(app, ["provider-verify", "p1"])

    assert result.exit_code == 0
    assert "active" in _text(result)


def test_connection_delete_and_repo_update_hit_their_endpoints(stub: StubClient) -> None:
    stub.responses[f"{PROJECT}/repositories/r1"] = {"full_name": "acme/api"}

    deleted = runner.invoke(app, ["connection-delete", "c1"])
    updated = runner.invoke(app, ["repo-update", "r1", "--window-days", "30"])

    assert deleted.exit_code == 0
    assert updated.exit_code == 0
    assert ("delete", "/orgs/org-1/git-connections/c1", None) in stub.calls
    assert (
        "patch",
        f"{PROJECT}/repositories/r1",
        {"sync_window_days": 30},
    ) in stub.calls


def test_repo_update_with_nothing_to_change_is_an_error(stub: StubClient) -> None:
    result = runner.invoke(app, ["repo-update", "r1"])

    assert result.exit_code == 1
    assert "Nothing to change" in _text(result)


class _FakeClock:
    """Stands in for `time` so the poll loop advances without real waiting."""

    def __init__(self, on_sleep: Callable[[], None]) -> None:
        self._on_sleep = on_sleep
        self._now = 0.0

    def monotonic(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += seconds
        self._on_sleep()


class _CopyingStub(StubClient):
    """A stub whose GETs return a fresh dict, like two separate HTTP responses."""

    def get(self, path: str, **params: Any) -> Any:
        value = super().get(path, **params)
        return dict(value) if isinstance(value, dict) else value


def _stale_failed_row() -> dict[str, Any]:
    """What a repository looks like after an earlier run failed."""
    return {
        "id": "r1",
        "full_name": "acme/era",
        "sync_status": "failed",
        "sync_error": "GitNotFoundError: GitHub returned 404 for /repos/acme/era/commits.",
        "last_synced_at": "2026-09-23T06:03:14Z",
    }


def test_sync_wait_reports_this_run_not_the_previous_failure(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row left `failed` by an earlier run must not be read as this run's result.

    Queueing a sync does not touch the row -- only the worker does, once it starts --
    so the first poll still sees the previous outcome.
    """
    client = _CopyingStub()
    path = f"{PROJECT}/repositories/r1"
    client.responses[path] = _stale_failed_row()
    monkeypatch.setattr(context, "client", lambda: client)

    steps = iter(
        [
            {"sync_status": "running"},
            {
                "sync_status": "succeeded",
                "sync_error": "",
                "last_synced_at": "2026-10-13T10:33:22Z",
            },
        ]
    )

    def advance() -> None:
        client.responses[path].update(next(steps))

    monkeypatch.setattr(repos, "time", _FakeClock(advance))

    result = runner.invoke(app, ["sync", "r1", "--wait", "--interval", "1"])

    text = _text(result)
    assert result.exit_code == 0, text
    assert "Synced acme/era" in text
    assert "404" not in text, "the previous run's error must not be reported"


def test_sync_wait_gives_up_when_the_run_never_starts(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unchanged terminal row is not proof the queued run happened."""
    client = _CopyingStub()
    client.responses[f"{PROJECT}/repositories/r1"] = _stale_failed_row()
    monkeypatch.setattr(context, "client", lambda: client)
    monkeypatch.setattr(repos, "time", _FakeClock(lambda: None))

    result = runner.invoke(app, ["sync", "r1", "--wait", "--interval", "5", "--timeout", "10"])

    assert result.exit_code == 1
    assert "did not finish within 10s" in _text(result)


def test_findings_worst_first_asks_the_server_to_rank_by_severity(stub: StubClient) -> None:
    stub.responses[f"{PROJECT}/ai/findings"] = []

    result = runner.invoke(app, ["findings", "--worst-first", "--severity", "high"])

    assert result.exit_code == 0
    call = next(call for call in stub.calls if call[1] == f"{PROJECT}/ai/findings")
    assert call[2] == {"severity": "high", "ordering": "-severity"}


def test_triage_posts_the_status_and_rejects_a_bad_one(stub: StubClient) -> None:
    stub.responses[f"{PROJECT}/ai/findings/f1/status"] = {"id": "f1", "status": "acknowledged"}

    accepted = runner.invoke(app, ["triage", "f1", "acknowledged"])
    refused = runner.invoke(app, ["triage", "f1", "done-for"])

    assert accepted.exit_code == 0
    assert "acknowledged" in accepted.output
    assert ("post", f"{PROJECT}/ai/findings/f1/status", {"status": "acknowledged"}) in stub.calls
    assert refused.exit_code == 1
    assert "Unknown status" in _text(refused)


def test_audit_lists_the_organization_trail(stub: StubClient) -> None:
    stub.responses["/orgs/org-1/audit-logs"] = [
        {
            "id": "a1",
            "actor_type": "user",
            "actor_id": "u1",
            "action": "ai_finding.status_changed",
            "target_type": "ai_finding",
            "target_id": "f1",
            "request_id": "req-1",
            "created_at": "2026-01-02T03:04:05Z",
        }
    ]

    result = runner.invoke(app, ["audit"])

    assert result.exit_code == 0
    assert "Audit log" in result.output
    # The action code is long enough that rich wraps it across lines, so the request is
    # asserted on rather than a substring of the rendered table.
    assert any(call[0] == "results" and call[1] == "/orgs/org-1/audit-logs" for call in stub.calls)


class _FakeFlow:
    """Stands in for the real device flow so 'login' never touches GitHub."""

    def __init__(self) -> None:
        self.closed = False

    def request_device_code(self, client_id: str, scopes: list[str]) -> SimpleNamespace:
        assert client_id == "Iv1.test-client"
        assert scopes == ["read:user", "user:email"]
        return SimpleNamespace(
            device_code="dc",
            user_code="WDJB-MJHT",
            verification_uri="https://github.com/login/device",
            expires_in=900,
            interval=1,
        )

    def poll_access_token(
        self, client_id: str, device_code: str, *, interval: int, **kwargs: Any
    ) -> str:
        assert device_code == "dc"
        return "gho_token"

    def close(self) -> None:
        self.closed = True


def test_login_runs_the_device_flow_and_exchanges_the_token(
    stub: StubClient, monkeypatch: Any
) -> None:
    captured: dict[str, _FakeFlow] = {}

    def make_flow() -> _FakeFlow:
        captured["flow"] = _FakeFlow()
        return captured["flow"]

    monkeypatch.setattr(auth, "GitHubDeviceFlow", make_flow)

    result = runner.invoke(app, ["login", "--no-browser"])

    assert result.exit_code == 0
    assert "WDJB-MJHT" in result.output
    assert "Signed in as octocat" in result.output
    assert ("login_with_github", "gho_token", None) in stub.calls
    assert captured["flow"].closed is True


def test_login_reports_a_server_without_a_client_id(stub: StubClient) -> None:
    stub.github_config_response = {"client_id": "", "scopes": []}

    result = runner.invoke(app, ["login", "--no-browser"])

    assert result.exit_code == 1
    assert "GITHUB_CLIENT_ID" in _text(result)


def test_org_add_creates_an_organization(stub: StubClient) -> None:
    stub.responses["/orgs"] = {"id": "org-9", "slug": "my-team", "name": "My Team"}

    result = runner.invoke(app, ["org-add", "My Team"])

    assert result.exit_code == 0
    assert "my-team" in result.output
    assert ("post", "/orgs", {"name": "My Team"}) in stub.calls


def test_status_shows_the_github_login(stub: StubClient) -> None:
    stub.responses["/me"] = {"github_login": "octocat", "email": "octocat@example.com"}

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "octocat" in result.output
