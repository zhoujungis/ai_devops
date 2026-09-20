"""The OpenAI wire format, which most vendors now speak.

DeepSeek, Qwen, Ollama and self-hosted gateways all expose an OpenAI-compatible
chat-completions endpoint, so they differ by base URL and model names rather than by
protocol. Four near-identical modules would be four copies of this file to keep in
sync, so the differences live in :data:`PROVIDER_PRESETS` instead.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from apps.ai.providers.base import (
    AIAuthenticationError,
    AIProvider,
    AIProviderError,
    AIRateLimitError,
    AIResponseError,
    ChatMessage,
    ChatResult,
    TimedCall,
    TokenUsage,
    ToolCall,
)

DEFAULT_TIMEOUT_SECONDS = 60.0


class OpenAICompatibleProvider(AIProvider):
    """Chat and embeddings over ``/chat/completions`` and ``/embeddings``."""

    def __init__(
        self,
        *,
        provider_type: str,
        base_url: str,
        api_key: str = "",
        transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.provider_type = provider_type
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenAICompatibleProvider:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(path, json=payload)
        if response.status_code == 401:
            raise AIAuthenticationError("The provider rejected the API key.")
        if response.status_code == 429:
            raise AIRateLimitError("The provider is rate limiting us.")
        if response.status_code >= 400:
            raise AIProviderError(
                f"{self.provider_type} returned {response.status_code}: {response.text[:300]}"
            )
        data: dict[str, Any] = response.json()
        return data

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [message.as_dict() for message in messages],
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        with TimedCall() as timer:
            data = self._post("/chat/completions", payload)

        choices = data.get("choices") or []
        if not choices:
            raise AIResponseError(f"{self.provider_type} returned no choices.")
        message = choices[0].get("message") or {}
        usage = data.get("usage") or {}

        return ChatResult(
            content=message.get("content") or "",
            model=data.get("model") or model,
            usage=TokenUsage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                # Both spellings are in the wild.
                cached_tokens=int(
                    (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
                ),
            ),
            latency_ms=timer.elapsed_ms,
            tool_calls=tuple(_to_tool_call(item) for item in message.get("tool_calls") or []),
            finish_reason=choices[0].get("finish_reason") or "",
            raw=data,
        )

    def embedding(self, texts: list[str], *, model: str) -> list[list[float]]:
        if not texts:
            return []
        data = self._post("/embeddings", {"model": model, "input": texts})
        rows = data.get("data") or []
        if len(rows) != len(texts):
            raise AIResponseError(
                f"Asked for {len(texts)} embeddings, got {len(rows)} back from "
                f"{self.provider_type}."
            )
        # The API does not promise ordering, only an `index`, so sort by it.
        ordered = sorted(rows, key=lambda row: row.get("index", 0))
        return [list(row["embedding"]) for row in ordered]


def _to_tool_call(payload: dict[str, Any]) -> ToolCall:
    function = payload.get("function") or {}
    raw_arguments = function.get("arguments") or "{}"
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError:
        # A model emitting malformed JSON arguments is a normal failure; hand back
        # something the tool layer can reject with a useful message.
        arguments = {"_unparsed": raw_arguments}
    return ToolCall(
        id=payload.get("id") or "",
        name=function.get("name") or "",
        arguments=arguments,
    )
