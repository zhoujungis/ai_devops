"""Prompts are versioned files, never strings in business code.

Two reasons this is files rather than constants:

* a prompt change is a behaviour change, so it needs a version that can be recorded
  against every run and rolled back independently of a code deploy;
* prompts are prose that deserves review and diffing like prose, not a triple-quoted
  blob buried in a service.

The frontmatter names the output schema, so a prompt cannot silently reference a
schema that does not exist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from apps.ai.schemas import SCHEMAS

PROMPT_ROOT = Path(__file__).resolve().parent

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_REQUIRED_KEYS = ("id", "version", "schema")


class PromptError(RuntimeError):
    """Raised when a prompt file is missing or malformed."""


@dataclass(frozen=True)
class PromptTemplate:
    prompt_id: str
    version: int
    system: str
    schema_name: str
    capability: str
    changelog: str
    path: str

    @property
    def schema(self) -> type[Any]:
        try:
            return SCHEMAS[self.schema_name]
        except KeyError as exc:
            raise PromptError(
                f"Prompt {self.prompt_id} v{self.version} names schema "
                f"{self.schema_name!r}, which is not registered in apps.ai.schemas."
            ) from exc


def parse_prompt_file(path: Path) -> PromptTemplate:
    raw = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(raw)
    if match is None:
        raise PromptError(f"{path} must start with a '---' frontmatter block.")

    try:
        meta: dict[str, Any] = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise PromptError(f"{path} has invalid YAML frontmatter: {exc}") from exc

    missing = [key for key in _REQUIRED_KEYS if key not in meta]
    if missing:
        raise PromptError(f"{path} is missing frontmatter keys: {', '.join(missing)}")

    body = match.group(2).strip()
    if not body:
        raise PromptError(f"{path} has an empty body.")

    return PromptTemplate(
        prompt_id=str(meta["id"]),
        version=int(meta["version"]),
        system=body,
        schema_name=str(meta["schema"]),
        capability=str(meta.get("capability", "chat")),
        changelog=str(meta.get("changelog", "")).strip(),
        path=str(path),
    )


class PromptRegistry:
    """Reads prompts from disk, once per instance.

    The cache is an instance attribute rather than ``lru_cache`` on the method: a
    method-level cache keys on ``self`` and therefore keeps every registry — and its
    root — alive for the process lifetime, which is the wrong trade for a handful of
    small files.
    """

    def __init__(self, root: Path = PROMPT_ROOT) -> None:
        self._root = root
        self._by_id: dict[str, list[PromptTemplate]] | None = None

    def _all(self) -> dict[str, list[PromptTemplate]]:
        if self._by_id is not None:
            return self._by_id

        by_id: dict[str, list[PromptTemplate]] = {}
        for path in sorted(self._root.glob("*/*.md")):
            template = parse_prompt_file(path)
            by_id.setdefault(template.prompt_id, []).append(template)
        for templates in by_id.values():
            templates.sort(key=lambda template: template.version)

        self._by_id = by_id
        return by_id

    def available(self) -> dict[str, list[int]]:
        return {
            prompt_id: [template.version for template in templates]
            for prompt_id, templates in self._all().items()
        }

    def load(self, prompt_id: str, version: int | None = None) -> PromptTemplate:
        """Load a prompt, defaulting to the newest version."""
        templates = self._all().get(prompt_id)
        if not templates:
            raise PromptError(
                f"No prompt named {prompt_id!r}. Available: "
                f"{', '.join(sorted(self._all())) or '(none)'}"
            )
        if version is None:
            return templates[-1]
        for template in templates:
            if template.version == version:
                return template
        raise PromptError(
            f"Prompt {prompt_id!r} has no version {version}. Available: "
            f"{[template.version for template in templates]}"
        )


registry = PromptRegistry()


def load_prompt(prompt_id: str, version: int | None = None) -> PromptTemplate:
    return registry.load(prompt_id, version)
