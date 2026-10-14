"""The release check and the upgrade command.

Nothing here touches the network: the fetcher is injected, the clock is injected, and
the cache is redirected to a temp path. What is being tested is the decision -- when to
ask, when to trust the cache, when to say anything at all.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ai_devops_cli import update
from ai_devops_cli.main import app

runner = CliRunner()


def _text(result: Any) -> str:
    """stdout plus stderr, whichever stream Click kept them on."""
    return result.output + (getattr(result, "stderr", "") or "")


@pytest.fixture(autouse=True)
def _checking(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the suite-wide switch-off: these tests are about the check itself."""
    monkeypatch.delenv(update.DISABLE_ENV, raising=False)


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "update-check.json"
    monkeypatch.setenv("AIDEVOPS_UPDATE_CACHE", str(path))
    return path


# ---------------------------------------------------------------------------
# Deciding whether there is anything to say
# ---------------------------------------------------------------------------
def test_a_newer_release_is_reported(cache: Path) -> None:
    assert update.newer_version(fetcher=lambda: "9.9.9") == "9.9.9"


def test_the_same_release_is_not_reported(cache: Path) -> None:
    assert update.newer_version(fetcher=lambda: update.__version__) is None


def test_an_older_release_is_not_reported(cache: Path) -> None:
    assert update.newer_version(fetcher=lambda: "0.0.1") is None


def test_an_unparseable_version_is_not_reported(cache: Path) -> None:
    """A local build with an odd version string must not nag its own author."""
    assert update.newer_version(fetcher=lambda: "not-a-version") is None


def test_a_lookup_that_fails_says_nothing(cache: Path) -> None:
    assert update.newer_version(fetcher=lambda: None) is None
    assert not cache.exists(), "a failed lookup must not be remembered"


def test_a_fresh_cache_answers_without_asking_again(cache: Path) -> None:
    calls: list[int] = []

    def fetcher() -> str:
        calls.append(1)
        return "9.9.9"

    assert update.newer_version(fetcher=fetcher) == "9.9.9"
    assert update.newer_version(fetcher=fetcher) == "9.9.9"
    assert len(calls) == 1


def test_an_aged_out_cache_is_refetched(cache: Path) -> None:
    cache.write_text(json.dumps({"latest": "9.9.9", "checked_at": 0}), encoding="utf-8")

    assert update.newer_version(fetcher=lambda: "8.8.8") == "8.8.8"


def test_a_fresh_cache_is_trusted_even_past_midnight(cache: Path) -> None:
    """The clock is injected so the boundary can be asserted, not slept through."""
    cache.write_text(json.dumps({"latest": "9.9.9", "checked_at": 1000}), encoding="utf-8")

    inside = update.newer_version(fetcher=lambda: None, clock=lambda: 1000 + 60)
    outside = update.newer_version(fetcher=lambda: "7.7.7", clock=lambda: 1000 + 10**9)

    assert inside == "9.9.9", "still inside the window, so the cache answers"
    assert outside == "7.7.7", "past the window, so PyPI is asked again"


def test_the_check_can_be_switched_off(cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CI and the test suite set this; the check must not reach the network then."""
    monkeypatch.setenv(update.DISABLE_ENV, "1")

    def fetcher() -> str:
        raise AssertionError("the check was disabled, so nothing should be fetched")

    assert update.newer_version(fetcher=fetcher) is None
    assert not cache.exists()


def test_forget_drops_the_cached_answer(cache: Path) -> None:
    update.newer_version(fetcher=lambda: "9.9.9")
    assert cache.exists()

    update.forget()

    assert not cache.exists()


# ---------------------------------------------------------------------------
# Where the hint goes
# ---------------------------------------------------------------------------
def test_the_hint_is_red_and_reaches_a_human(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(update, "newer_version", lambda **_: "9.9.9")

    result = runner.invoke(app, ["version"])

    assert "9.9.9" in result.output
    assert "aidevops upgrade" in result.output


def test_the_hint_goes_to_stderr_when_stdout_is_a_payload(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A script piping stdout into jq should not have to defend itself against this."""
    monkeypatch.setattr(update, "newer_version", lambda **_: "9.9.9")

    result = runner.invoke(app, ["--json", "version"])

    # Click's `output` is both streams concatenated; only `stdout` is the payload.
    assert "9.9.9" not in result.stdout
    assert "9.9.9" in (result.stderr or "")


def test_a_bare_invocation_prints_help_and_still_checks(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`no_args_is_help` would print help before the callback and hide the hint."""
    monkeypatch.setattr(update, "newer_version", lambda **_: "9.9.9")

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "9.9.9" in result.output, "the hint must survive the help path"
    assert "Typical first run" in result.output


def test_upgrade_is_not_preceded_by_a_hint(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Telling someone to run the command they are running is noise."""
    monkeypatch.setattr(update, "is_development_checkout", lambda: False)
    monkeypatch.setattr(update, "newer_version", lambda **_: None)
    monkeypatch.setattr(update, "hint", lambda: pytest.fail("upgrade must skip the hint"))

    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# The upgrade command
# ---------------------------------------------------------------------------
def test_upgrade_refuses_a_development_checkout(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise `pip install -e` silently becomes the published release."""
    monkeypatch.setattr(update, "is_development_checkout", lambda: True)

    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 1
    assert "local directory" in _text(result)


def test_upgrade_says_so_when_already_current(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(update, "is_development_checkout", lambda: False)
    monkeypatch.setattr(update, "newer_version", lambda **_: None)

    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert "Already on the newest release" in result.output


def test_upgrade_installs_and_drops_the_cached_answer(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps: list[str] = []

    def run() -> int:
        steps.append("install")
        return 0

    monkeypatch.setattr(update, "is_development_checkout", lambda: False)
    monkeypatch.setattr(update, "newer_version", lambda **_: "9.9.9")
    monkeypatch.setattr(update, "run_upgrade", run)
    monkeypatch.setattr(update, "forget", lambda: steps.append("forget"))

    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert steps == ["install", "forget"], "the cache describes the version we just left"
    assert "9.9.9" in result.output


def test_a_failed_upgrade_is_reported_not_swallowed(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(update, "is_development_checkout", lambda: False)
    monkeypatch.setattr(update, "newer_version", lambda **_: "9.9.9")
    monkeypatch.setattr(update, "run_upgrade", lambda: 1)

    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 1
    assert "Upgrade failed" in _text(result)


def test_pipx_installations_are_upgraded_through_pipx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pipx owns the virtualenv; pip inside it would be undone by the next upgrade."""
    monkeypatch.setattr(sys, "prefix", "C:/Users/x/.local/pipx/venvs/aidevops")

    assert update.upgrade_command() == ["pipx", "upgrade", "aidevops"]


def test_a_plain_install_is_upgraded_with_pip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "prefix", "C:/Python313")
    monkeypatch.setattr(sys, "base_prefix", "C:/Python313")

    command = update.upgrade_command()

    assert command[1:] == ["-m", "pip", "install", "--upgrade", "aidevops"]


class _Distribution:
    """Stands in for importlib.metadata's view of the installed package."""

    def __init__(self, direct_url: str | None) -> None:
        self._direct_url = direct_url

    def read_text(self, name: str) -> str | None:
        assert name == "direct_url.json"
        return self._direct_url


def test_an_editable_install_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    editable = json.dumps({"url": "file:///D:/ai_devops/cli", "dir_info": {"editable": True}})
    monkeypatch.setattr(update, "distribution", lambda _name: _Distribution(editable))

    assert update.is_development_checkout() is True


def test_a_published_install_is_not_a_development_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No direct_url.json at all is what a wheel installed from PyPI looks like."""
    monkeypatch.setattr(update, "distribution", lambda _name: _Distribution(None))

    assert update.is_development_checkout() is False


def test_a_non_editable_local_install_is_not_treated_as_a_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = json.dumps({"url": "file:///tmp/aidevops-0.2.0.tar.gz", "archive_info": {}})
    monkeypatch.setattr(update, "distribution", lambda _name: _Distribution(archive))

    assert update.is_development_checkout() is False
