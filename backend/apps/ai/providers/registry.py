"""Choosing a provider, and choosing a model for a capability.

Business code asks for a capability ("chat", "embedding") and this module decides
which vendor and which model serve it. That indirection is the whole point: swapping
gpt-4o for a local model must not require touching an agent.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from apps.ai.models import AIProviderConfig, AIProviderType, ProviderStatus
from apps.ai.providers.base import AIProvider, AIProviderError
from apps.ai.providers.openai_compat import DEFAULT_TIMEOUT_SECONDS, OpenAICompatibleProvider

CAPABILITY_CHAT = "chat"
CAPABILITY_EMBEDDING = "embedding"
CAPABILITY_VISION = "vision"


@dataclass(frozen=True)
class ProviderPreset:
    """Everything that actually differs between OpenAI-compatible vendors."""

    base_url: str
    supports_embeddings: bool = True
    #: Environment variable to fall back to when the row has no key stored.
    api_key_env: str = ""


PROVIDER_PRESETS: Mapping[str, ProviderPreset] = {
    AIProviderType.OPENAI: ProviderPreset(
        base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY"
    ),
    AIProviderType.DEEPSEEK: ProviderPreset(
        base_url="https://api.deepseek.com/v1",
        # DeepSeek serves chat only; embeddings must come from another provider.
        supports_embeddings=False,
        api_key_env="DEEPSEEK_API_KEY",
    ),
    AIProviderType.QWEN: ProviderPreset(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="QWEN_API_KEY",
    ),
    AIProviderType.OLLAMA: ProviderPreset(
        base_url="http://127.0.0.1:11434/v1",
        api_key_env="OLLAMA_API_KEY",
    ),
    AIProviderType.OPENAI_COMPATIBLE: ProviderPreset(base_url=""),
}


def build_provider(
    config: AIProviderConfig,
    *,
    transport: Any = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> AIProvider:
    """Construct the provider described by a stored configuration."""
    preset = PROVIDER_PRESETS.get(config.provider_type)
    if preset is None:
        raise AIProviderError(f"Unknown provider type: {config.provider_type!r}")

    base_url = config.base_url or preset.base_url
    if not base_url:
        raise AIProviderError(
            f"Provider {config.label!r} has no base URL and its type has no default."
        )

    return OpenAICompatibleProvider(
        provider_type=config.provider_type,
        base_url=base_url,
        api_key=config.api_key,
        transport=transport,
        timeout=timeout,
    )


def default_config(org: Any) -> AIProviderConfig | None:
    """The organization's default provider, falling back to its only one.

    Fetched once and decided in Python: the previous version ran three queries
    (default, count, first) to answer a question one row read can settle.
    """
    active = list(
        AIProviderConfig.objects.filter(org=org).exclude(status=ProviderStatus.DISABLED)
    )
    for config in active:
        if config.is_default:
            return config
    return active[0] if len(active) == 1 else None


@dataclass(frozen=True)
class ResolvedModel:
    """A provider instance plus the model to use for one capability."""

    config: AIProviderConfig
    provider: AIProvider
    model: str

    def close(self) -> None:
        self.provider.close()


def resolve(org: Any, capability: str) -> ResolvedModel:
    """The provider and model that serve ``capability`` for this organization."""
    config = default_config(org)
    if config is None:
        raise AIProviderError(
            "No AI provider is configured for this organization. Add one under "
            "organization settings before running an analysis."
        )

    model = config.model_for(capability)
    if not model:
        raise AIProviderError(
            f"Provider {config.label!r} has no model configured for capability " f"{capability!r}."
        )

    preset = PROVIDER_PRESETS.get(config.provider_type)
    if capability == CAPABILITY_EMBEDDING and preset and not preset.supports_embeddings:
        raise AIProviderError(
            f"{config.provider_type} does not serve embeddings; configure a provider "
            "that does for the embedding capability."
        )

    return ResolvedModel(config=config, provider=build_provider(config), model=model)
