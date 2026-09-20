"""A scripted provider, so agent and provider logic is tested without a network.

Records every call it receives, which is what lets tests assert *what the agent
asked for* rather than only what it got back.
"""

from __future__ import annotations

from typing import Any

from apps.ai.providers.base import (
    AIProvider,
    ChatMessage,
    ChatResult,
    TokenUsage,
    ToolCall,
)


class FakeAIProvider(AIProvider):
    provider_type = "fake"

    def __init__(
        self,
        *,
        replies: list[str] | None = None,
        tool_calls: list[tuple[ToolCall, ...]] | None = None,
        embedding_vector: list[float] | None = None,
    ) -> None:
        self._replies = list(replies or [])
        self._tool_calls = list(tool_calls or [])
        self._embedding_vector = embedding_vector or [0.1, 0.2, 0.3]
        self.calls: list[list[ChatMessage]] = []
        self.embedding_calls: list[list[str]] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        self.calls.append(list(messages))
        content = self._replies.pop(0) if self._replies else "{}"
        calls = self._tool_calls.pop(0) if self._tool_calls else ()
        return ChatResult(
            content=content,
            model=model,
            usage=TokenUsage(input_tokens=100, output_tokens=20),
            latency_ms=7,
            tool_calls=calls,
        )

    def embedding(self, texts: list[str], *, model: str) -> list[list[float]]:
        self.embedding_calls.append(list(texts))
        return [list(self._embedding_vector) for _ in texts]


def make_tool_call(name: str, arguments: dict[str, Any], call_id: str = "call-1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)
