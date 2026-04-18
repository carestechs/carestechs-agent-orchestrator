"""Happy-path tests for AnthropicLLMProvider.chat_with_tools (T-066)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from app.config import Settings
from app.core.llm import ToolDefinition
from app.core.llm_anthropic import AnthropicLLMProvider

_BASE_KW: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "orchestrator_api_key": "k",
    "engine_webhook_secret": "s",
    "engine_base_url": "http://engine.test",
}


def _settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        **_BASE_KW,
        "llm_provider": "anthropic",
        "anthropic_api_key": "sk-ant-test-xxx",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _tool_use_response(
    *,
    tool_name: str = "analyze_brief",
    tool_input: dict[str, Any] | None = None,
    input_tokens: int = 42,
    output_tokens: int = 7,
    leading_text: str | None = None,
    tool_use_id: str = "tu_test_123",
) -> dict[str, Any]:
    """Anthropic Messages API response with one ``tool_use`` content block."""
    content: list[dict[str, Any]] = []
    if leading_text is not None:
        content.append({"type": "text", "text": leading_text})
    content.append(
        {
            "type": "tool_use",
            "id": tool_use_id,
            "name": tool_name,
            "input": tool_input if tool_input is not None else {},
        }
    )
    return {
        "id": "msg_test_abc",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-4-7",
        "content": content,
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


_ANALYZE_TOOL = ToolDefinition(
    name="analyze_brief",
    description="Read the intake brief and extract key points.",
    parameters={
        "type": "object",
        "properties": {"brief": {"type": "string"}},
        "required": ["brief"],
    },
)


class TestHappyPath:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_tool_call_with_name_and_arguments(self) -> None:
        provider = AnthropicLLMProvider(_settings())
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            mock.post("/v1/messages").mock(
                return_value=httpx.Response(
                    200,
                    json=_tool_use_response(
                        tool_name="analyze_brief",
                        tool_input={"brief": "hi"},
                    ),
                )
            )
            result = await provider.chat_with_tools(
                system="SYS",
                messages=[{"role": "user", "content": "hello"}],
                tools=[_ANALYZE_TOOL],
            )
        assert result.name == "analyze_brief"
        assert result.arguments == {"brief": "hi"}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_usage_populated(self) -> None:
        provider = AnthropicLLMProvider(_settings())
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            mock.post("/v1/messages").mock(
                return_value=httpx.Response(
                    200,
                    json=_tool_use_response(input_tokens=42, output_tokens=7),
                )
            )
            result = await provider.chat_with_tools(
                system="SYS",
                messages=[{"role": "user", "content": "hello"}],
                tools=[_ANALYZE_TOOL],
            )
        assert result.usage.input_tokens == 42
        assert result.usage.output_tokens == 7
        assert result.usage.latency_ms >= 0  # perf_counter can round tiny diffs to 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_tool_translation_uses_input_schema_key(self) -> None:
        provider = AnthropicLLMProvider(_settings())
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            route = mock.post("/v1/messages").mock(
                return_value=httpx.Response(200, json=_tool_use_response())
            )
            await provider.chat_with_tools(
                system="SYS",
                messages=[{"role": "user", "content": "hello"}],
                tools=[_ANALYZE_TOOL],
            )
        body = json.loads(route.calls.last.request.content)
        assert body["tools"] == [
            {
                "name": "analyze_brief",
                "description": "Read the intake brief and extract key points.",
                "input_schema": {
                    "type": "object",
                    "properties": {"brief": {"type": "string"}},
                    "required": ["brief"],
                },
            }
        ]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_system_and_messages_forwarded_verbatim(self) -> None:
        provider = AnthropicLLMProvider(_settings())
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            route = mock.post("/v1/messages").mock(
                return_value=httpx.Response(200, json=_tool_use_response())
            )
            await provider.chat_with_tools(
                system="SYSTEM_PROMPT_HERE",
                messages=[
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "second"},
                ],
                tools=[_ANALYZE_TOOL],
            )
        body = json.loads(route.calls.last.request.content)
        assert body["system"] == "SYSTEM_PROMPT_HERE"
        assert body["messages"] == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
        ]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_raw_response_contains_only_whitelisted_keys(self) -> None:
        provider = AnthropicLLMProvider(_settings())
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            mock.post("/v1/messages").mock(
                return_value=httpx.Response(200, json=_tool_use_response())
            )
            result = await provider.chat_with_tools(
                system="SYS",
                messages=[{"role": "user", "content": "hello"}],
                tools=[_ANALYZE_TOOL],
            )
        assert result.raw_response is not None
        allowed = {
            "id",
            "type",
            "role",
            "model",
            "stop_reason",
            "stop_sequence",
            "usage",
            "content",
        }
        assert set(result.raw_response.keys()).issubset(allowed)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_leading_text_block_is_skipped(self) -> None:
        """A ``text`` block preceding the ``tool_use`` block must not confuse the parser."""
        provider = AnthropicLLMProvider(_settings())
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            mock.post("/v1/messages").mock(
                return_value=httpx.Response(
                    200,
                    json=_tool_use_response(leading_text="thinking aloud…"),
                )
            )
            result = await provider.chat_with_tools(
                system="SYS",
                messages=[{"role": "user", "content": "hello"}],
                tools=[_ANALYZE_TOOL],
            )
        assert result.name == "analyze_brief"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_model_name_sent_in_request(self) -> None:
        provider = AnthropicLLMProvider(_settings(llm_model="claude-sonnet-4-6"))
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            route = mock.post("/v1/messages").mock(
                return_value=httpx.Response(200, json=_tool_use_response())
            )
            await provider.chat_with_tools(
                system="SYS",
                messages=[{"role": "user", "content": "hello"}],
                tools=[_ANALYZE_TOOL],
            )
        body = json.loads(route.calls.last.request.content)
        assert body["model"] == "claude-sonnet-4-6"
