"""Model credentials.

Organizations own these, and only an admin may see or change them, because an API key
is a billable secret. This is the terminal's answer to "where do I point the AI" —
without it the product's core capability could only be configured over raw HTTP.
"""

from __future__ import annotations

from typing import Any

import typer

from ai_devops_cli import context, output
from ai_devops_cli.errors import CliError

#: Mirrors the backend's AIProviderType choices; ollama is the one that needs no key.
_PROVIDER_TYPES = ("openai", "deepseek", "qwen", "ollama", "openai_compatible")


def list_providers() -> None:
    """List the organization's model endpoints (admin only)."""
    rows = context.client().results(f"{context.org_base()}/ai-providers")
    if output.is_json():
        output.emit(rows)
        return
    output.table(
        "AI providers",
        ["ID", "LABEL", "TYPE", "DEFAULT", "KEY", "STATUS"],
        [
            [
                row["id"],
                row["label"],
                row["provider_type"],
                "*" if row["is_default"] else "",
                "yes" if row["has_api_key"] else "no",
                row["status"],
            ]
            for row in rows
        ],
        empty="No providers. Add one with 'copilot provider-add'.",
    )


def add_provider(
    label: str = typer.Option(..., "--label", "-l", help="A name you will recognise."),
    provider_type: str = typer.Option(
        "openai", "--type", help="openai|deepseek|qwen|ollama|openai_compatible."
    ),
    model: list[str] | None = typer.Option(
        None, "--model", "-m", help="capability=model, e.g. chat=gpt-4o (repeatable)."
    ),
    api_key: str = typer.Option("", "--api-key", help="Prompted when omitted."),
    base_url: str = typer.Option("", "--base-url", help="Leave blank for the preset default."),
    is_default: bool = typer.Option(False, "--default", help="Serve the capabilities it covers."),
) -> None:
    """Register a model endpoint so analyses can run."""
    if provider_type not in _PROVIDER_TYPES:
        raise CliError(
            f"Unknown provider type {provider_type!r}. One of: {', '.join(_PROVIDER_TYPES)}."
        )

    capability_models: dict[str, str] = {}
    for item in model or []:
        if "=" not in item:
            raise CliError(f"--model expects capability=model, got {item!r}.")
        capability, name = item.split("=", 1)
        capability_models[capability] = name

    if not api_key and provider_type != "ollama":
        api_key = typer.prompt("API key", hide_input=True)

    body: dict[str, Any] = {
        "label": label,
        "provider_type": provider_type,
        "capability_models": capability_models,
        "is_default": is_default,
    }
    if api_key:
        body["api_key"] = api_key
    if base_url:
        body["base_url"] = base_url

    row = context.client().post(f"{context.org_base()}/ai-providers", json=body)
    output.success(f"Created provider {row['id']} ({row['label']}).")
    if not capability_models:
        output.warn("No capability mapping given: add one with --model chat=<model>.")


def remove_provider(
    provider_id: str = typer.Argument(..., help="Provider id (see 'copilot providers')."),
) -> None:
    """Delete a model endpoint."""
    context.client().delete(f"{context.org_base()}/ai-providers/{provider_id}")
    output.success("Provider removed.")


def verify_provider(
    provider_id: str = typer.Argument(..., help="Provider id (see 'copilot providers')."),
) -> None:
    """Check the stored key against the endpoint, before an analysis finds it dead."""
    result = context.client().post(f"{context.org_base()}/ai-providers/{provider_id}/verify")
    if output.is_json():
        output.emit(result)
        return
    if result["verified"]:
        output.success(f"Verified ({result['status']}).")
    else:
        output.warn(f"Credentials are {result['status']}.")
