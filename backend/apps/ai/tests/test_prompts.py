"""The prompt registry: versioned files, schema binding, and loud failures."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.ai.prompts.registry import PromptError, PromptRegistry, load_prompt
from apps.ai.schemas import CodeImpactOutput


def test_the_code_impact_prompt_loads_with_its_schema() -> None:
    template = load_prompt("code_impact")

    assert template.prompt_id == "code_impact"
    assert template.version == 1
    assert template.schema is CodeImpactOutput
    assert template.capability == "chat"
    assert "risk score" in template.system


def test_the_newest_version_is_the_default() -> None:
    assert load_prompt("code_impact").version == 1


def test_available_lists_versions_per_prompt() -> None:
    available = PromptRegistry().available()

    assert available["code_impact"] == [1]


def test_an_unknown_prompt_lists_what_exists() -> None:
    with pytest.raises(PromptError, match="No prompt named"):
        load_prompt("does_not_exist")


def test_an_unknown_version_lists_what_exists() -> None:
    with pytest.raises(PromptError, match="no version 99"):
        load_prompt("code_impact", 99)


def test_a_prompt_without_frontmatter_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "v1.md").write_text("just a body, no frontmatter", encoding="utf-8")

    with pytest.raises(PromptError, match="frontmatter"):
        PromptRegistry(root=tmp_path).available()


def test_a_prompt_missing_required_keys_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "partial").mkdir()
    (tmp_path / "partial" / "v1.md").write_text("---\nid: partial\n---\nbody\n", encoding="utf-8")

    with pytest.raises(PromptError, match="missing frontmatter keys"):
        PromptRegistry(root=tmp_path).available()


def test_a_prompt_naming_an_unregistered_schema_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "ghost").mkdir()
    (tmp_path / "ghost" / "v1.md").write_text(
        "---\nid: ghost\nversion: 1\nschema: NotASchema\n---\nbody\n", encoding="utf-8"
    )

    template = PromptRegistry(root=tmp_path).load("ghost")

    with pytest.raises(PromptError, match="not registered"):
        _ = template.schema


def test_an_empty_body_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "v1.md").write_text(
        "---\nid: empty\nversion: 1\nschema: CodeImpactOutput\n---\n\n", encoding="utf-8"
    )

    with pytest.raises(PromptError, match="empty body"):
        PromptRegistry(root=tmp_path).available()
