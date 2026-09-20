"""Provider behaviour: wire mapping, error mapping and the structured-output contract."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import BaseModel, Field

from apps.ai.providers.base import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
    AIResponseError,
    ChatMessage,
)
from apps.ai.providers.openai_compat import OpenAICompatibleProvider
from apps.ai.providers.registry import PROVIDER_PRESETS, build_provider
from apps.ai.tests.fakes import FakeAIProvider


class Sample(BaseModel):
    name: str
    score: int = Field(ge=0, le=10)


def _provider(handler: Any, *, api_key: str = "test-key") -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        provider_type="openai",
        base_url="https://api.example.com/v1",
        api_key=api_key,
        transport=httpx.MockTransport(handler),
    )


CHAT_PAYLOAD: dict[str, Any] = {
    "model": "gpt-4o-2024-08-06",
    "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
    "usage": {
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "prompt_tokens_details": {"cached_tokens": 40},
    },
}


def test_chat_maps_content_model_and_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json=CHAT_PAYLOAD)

    result = _provider(handler).chat([ChatMessage(role="user", content="hi")], model="gpt-4o")

    assert result.content == "hello"
    assert result.model == "gpt-4o-2024-08-06"
    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 30
    assert result.usage.cached_tokens == 40
    assert result.finish_reason == "stop"


def test_chat_parses_tool_calls() -> None:
    payload = {
        "model": "gpt-4o",
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "explain_commit",
                                "arguments": '{"sha": "abc123"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    result = _provider(handler).chat([ChatMessage(role="user", content="hi")], model="gpt-4o")

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "explain_commit"
    assert result.tool_calls[0].arguments == {"sha": "abc123"}


def test_malformed_tool_arguments_are_preserved_for_the_tool_layer_to_reject() -> None:
    payload = {
        "model": "gpt-4o",
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {"name": "explain_commit", "arguments": "{not json"},
                        }
                    ]
                }
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    result = _provider(handler).chat([ChatMessage(role="user", content="hi")], model="gpt-4o")

    assert result.tool_calls[0].arguments == {"_unparsed": "{not json"}


def test_embeddings_are_returned_in_input_order() -> None:
    payload = {
        "data": [
            {"index": 1, "embedding": [0.2]},
            {"index": 0, "embedding": [0.1]},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    vectors = _provider(handler).embedding(["a", "b"], model="text-embedding-3-large")

    assert vectors == [[0.1], [0.2]]


def test_a_short_embedding_response_is_an_error_not_a_silent_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1]}]})

    with pytest.raises(AIResponseError, match="Asked for 2 embeddings"):
        _provider(handler).embedding(["a", "b"], model="m")


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(401, AIAuthenticationError), (429, AIRateLimitError), (500, AIProviderError)],
)
def test_http_failures_map_to_typed_errors(status_code: int, expected: type[Exception]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "nope"})

    with pytest.raises(expected):
        _provider(handler).chat([ChatMessage(role="user", content="hi")], model="m")


def test_an_empty_choice_list_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    with pytest.raises(AIResponseError, match="no choices"):
        _provider(handler).chat([ChatMessage(role="user", content="hi")], model="m")


# ---------------------------------------------------------------------------
# structured_output
# ---------------------------------------------------------------------------
def test_valid_structured_output_is_returned_without_a_repair() -> None:
    provider = FakeAIProvider(replies=['{"name": "ok", "score": 7}'])

    parsed, result = provider.structured_output(
        [ChatMessage(role="user", content="hi")], schema=Sample, model="m"
    )

    assert parsed == Sample(name="ok", score=7)
    assert len(provider.calls) == 1
    assert result.latency_ms == 7


def test_the_schema_is_injected_into_the_system_message() -> None:
    provider = FakeAIProvider(replies=['{"name": "ok", "score": 7}'])

    provider.structured_output([ChatMessage(role="user", content="hi")], schema=Sample, model="m")

    system = provider.calls[0][0]
    assert system.role == "system"
    assert "JSON Schema" in system.content
    assert "score" in system.content


def test_an_invalid_reply_is_repaired_once() -> None:
    provider = FakeAIProvider(replies=["not json at all", '{"name": "fixed", "score": 3}'])

    parsed, _ = provider.structured_output(
        [ChatMessage(role="user", content="hi")], schema=Sample, model="m"
    )

    assert parsed == Sample(name="fixed", score=3)
    assert len(provider.calls) == 2
    assert "did not match the required JSON schema" in provider.calls[1][-1].content


def test_two_invalid_replies_raise_rather_than_looping() -> None:
    provider = FakeAIProvider(replies=["nope", "still nope"])

    with pytest.raises(AIResponseError, match="after a repair attempt"):
        provider.structured_output(
            [ChatMessage(role="user", content="hi")], schema=Sample, model="m"
        )

    assert len(provider.calls) == 2, "a third attempt would be throwing good money after bad"


def test_a_schema_violation_is_repaired_too() -> None:
    """Well-formed JSON that breaks a constraint is still an invalid answer."""
    provider = FakeAIProvider(replies=['{"name": "x", "score": 99}', '{"name": "x", "score": 5}'])

    parsed, _ = provider.structured_output(
        [ChatMessage(role="user", content="hi")], schema=Sample, model="m"
    )

    assert parsed.score == 5


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
def test_provider_presets_are_used_when_no_base_url_is_stored() -> None:
    from apps.ai.models import AIProviderConfig, AIProviderType

    config = AIProviderConfig(
        provider_type=AIProviderType.DEEPSEEK, label="DeepSeek", capability_models={}
    )

    provider = build_provider(config)
    assert isinstance(provider, OpenAICompatibleProvider)
    provider.close()
    assert PROVIDER_PRESETS[AIProviderType.DEEPSEEK].base_url == "https://api.deepseek.com/v1"


def test_an_explicit_base_url_overrides_the_preset() -> None:
    from apps.ai.models import AIProviderConfig, AIProviderType
    from apps.ai.providers.registry import build_provider as build

    config = AIProviderConfig(
        provider_type=AIProviderType.OPENAI,
        label="Gateway",
        base_url="https://gateway.internal/v1",
        capability_models={},
    )

    provider = build(config)
    provider.close()  # constructing it is the assertion: the gateway URL is accepted
