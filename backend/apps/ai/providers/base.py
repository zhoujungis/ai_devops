"""The provider contract.

Two methods are required — ``chat`` and ``embedding`` — because those are what the
wire protocols actually differ on. Structured output and the tool loop are built
*on top of* ``chat`` in this base class, so a provider that lacks native support
(most of them, for structured output) still works: the schema is injected into the
prompt, the reply is parsed and validated, and one repair round-trip is attempted
before giving up.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

TSchema = TypeVar("TSchema", bound=BaseModel)


class AIProviderError(RuntimeError):
    """Base class for provider failures."""


class AIAuthenticationError(AIProviderError):
    """The provider rejected the credentials."""


class AIRateLimitError(AIProviderError):
    """The provider asked us to slow down."""


class AIResponseError(AIProviderError):
    """The provider returned something we could not use."""


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
        )


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: int = 0
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatMessage:
    """A provider-neutral message.

    Deliberately not the vendor's shape: the vendor's shape is an implementation
    detail of whichever module implements this protocol.
    """

    role: str
    content: str = ""
    name: str = ""
    tool_call_id: str = ""
    #: Set on assistant turns that requested tools.
    tool_calls: tuple[ToolCall, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            payload["name"] = self.name
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in self.tool_calls
            ]
        return payload


class AIProvider(ABC):
    """What the rest of the system needs from a model vendor."""

    provider_type: str = ""

    @abstractmethod
    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """One completion, optionally with tools available."""

    @abstractmethod
    def embedding(self, texts: list[str], *, model: str) -> list[list[float]]:
        """Embed each text, preserving order."""

    def close(self) -> None:
        """Release HTTP resources. A stateless provider has nothing to release."""
        return None

    # ------------------------------------------------------------------
    # Derived capabilities
    # ------------------------------------------------------------------
    def tool_call(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        tools: list[dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """Alias for :meth:`chat` with tools, kept for symmetry with the plan."""
        return self.chat(
            messages,
            model=model,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def structured_output(
        self,
        messages: list[ChatMessage],
        *,
        schema: type[TSchema],
        model: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> tuple[TSchema, ChatResult]:
        """Ask for JSON matching ``schema``, with one repair attempt.

        Returns the validated object plus the result it came from, so the caller can
        still record tokens and latency for the attempt that succeeded.

        A model that returns almost-JSON is normal, not exceptional, so one repair
        round-trip is cheaper than failing the whole analysis. Beyond one attempt we
        stop: a model that cannot produce the shape twice will not produce it on the
        fifth try either.
        """
        instructions = _schema_instructions(schema)
        prepared = list(messages)
        if prepared and prepared[0].role == "system":
            prepared[0] = ChatMessage(
                role="system", content=f"{prepared[0].content}\n\n{instructions}"
            )
        else:
            prepared.insert(0, ChatMessage(role="system", content=instructions))

        result = self.chat(prepared, model=model, temperature=temperature, max_tokens=max_tokens)
        try:
            return schema.model_validate_json(result.content), result
        except ValidationError as first_error:
            repair = [
                *prepared,
                ChatMessage(role="assistant", content=result.content),
                ChatMessage(
                    role="user",
                    content=(
                        "That reply did not match the required JSON schema:\n"
                        f"{first_error}\n\n"
                        "Return only corrected JSON."
                    ),
                ),
            ]
            repaired = self.chat(
                repair, model=model, temperature=temperature, max_tokens=max_tokens
            )
            try:
                return schema.model_validate_json(repaired.content), repaired
            except ValidationError as second_error:
                raise AIResponseError(
                    f"Model did not return valid {schema.__name__} JSON after a repair "
                    f"attempt: {second_error}"
                ) from second_error


def _schema_instructions(schema: type[BaseModel]) -> str:
    return (
        "Reply with a single JSON object and nothing else — no prose, no markdown "
        "fence. It must validate against this JSON Schema:\n"
        f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
    )


class TimedCall:
    """Context manager that measures a provider call in milliseconds."""

    def __enter__(self) -> TimedCall:
        self._start = time.perf_counter()
        self.elapsed_ms = 0
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.elapsed_ms = int((time.perf_counter() - self._start) * 1000)
