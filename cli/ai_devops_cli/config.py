"""Where the CLI keeps its settings and tokens.

One JSON file per machine. It holds refresh and access tokens, so it is written
with owner-only permissions where the platform supports it. Everything can be
overridden by environment variables, which is what makes the tool usable in CI:
``COPILOT_CONFIG`` (file location), ``COPILOT_BASE_URL`` and ``COPILOT_TOKEN``.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import platformdirs

APP_NAME = "ai-devops-copilot"
DEFAULT_BASE_URL = "http://127.0.0.1:8000/api/v1"


def config_path() -> Path:
    override = os.environ.get("COPILOT_CONFIG")
    if override:
        return Path(override)
    return Path(platformdirs.user_config_dir(APP_NAME)) / "config.json"


@dataclass
class Settings:
    """Persisted client state. IDs, not objects, so nothing here goes stale."""

    base_url: str = DEFAULT_BASE_URL
    access: str = ""
    refresh: str = ""
    email: str = ""
    org_id: str = ""
    org_name: str = ""
    project_id: str = ""
    project_name: str = ""

    @property
    def logged_in(self) -> bool:
        return bool(self.refresh or self.access)

    @property
    def has_project(self) -> bool:
        return bool(self.org_id and self.project_id)

    def clear_project(self) -> None:
        self.org_id = ""
        self.org_name = ""
        self.project_id = ""
        self.project_name = ""

    def clear_tokens(self) -> None:
        self.access = ""
        self.refresh = ""


def load() -> Settings:
    settings = Settings()
    path = config_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        known = {field.name for field in fields(Settings)}
        for key, value in data.items():
            if key in known and isinstance(value, str):
                setattr(settings, key, value)

    # Environment wins over the file, so CI can point the CLI without writing one.
    if os.environ.get("COPILOT_BASE_URL"):
        settings.base_url = os.environ["COPILOT_BASE_URL"]
    if os.environ.get("COPILOT_TOKEN"):
        settings.access = os.environ["COPILOT_TOKEN"]
    return settings


def save(settings: Settings) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    # Windows and some filesystems do not implement POSIX modes; the file is still
    # only readable by the current user in the places where that matters.
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
