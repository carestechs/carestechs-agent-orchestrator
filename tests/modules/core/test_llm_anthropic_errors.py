"""Error-mapping tests for AnthropicLLMProvider.chat_with_tools (T-067)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from app.config import Settings
from app.core.exceptions import PolicyError, ProviderError
from app.core.llm import ToolDefinition
from app.core.llm_anthropic import AnthropicLLMProvider

_BASE_KW: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "orchestrator_api_key": "k",
    "engine_webhook_secret": "s",
    "engine_base_url": "http://engine.test",
}


def _settings() -> Settings:
    return Settings(
        **_BASE_KW,  # type: ignore[arg-type]
        llm_provider="anthropic",
        anthropic_api_key="sk-ant-test-xxx",
    )


_TOOL = ToolDefinition(
    name="analyze_brief",
    description="x",
    parameters={"type": "object", "properties": {}},
)


async def _call(provider: AnthropicLLMProvider) -> Any:
    return await provider.chat_with_tools(
        system="SYS",
        messages=[{"role": "user", "content": "hi"}],
        tools=[_TOOL],
    )


def _base_response(content: list[dict[str, Any]], stop_reason: str = "tool_use") -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-4-7",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 5, "output_tokens": 1},
    }


# ---------------------------------------------------------------------------
# Transport / HTTP status errors
# ---------------------------------------------------------------------------


class TestHttpErrors:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_500_raises_provider_error_with_status_and_request_id(self) -> None:
        provider = AnthropicLLMProvider(_settings())
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            mock.post("/v1/messages").mock(
                return_value=httpx.Response(
                    500,
                    text="server boom",
                    headers={"request-id": "req-xyz"},
                )
            )
            with pytest.raises(ProviderError) as exc_info:
                await _call(provider)
        assert exc_info.value.provider_http_status == 500
        assert exc_info.value.provider_request_id == "req-xyz"
        assert exc_info.value.original_body == "server boom"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_401_raises_provider_error(self) -> None:
        provider = AnthropicLLMProvider(_settings())
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            mock.post("/v1/messages").mock(
                return_value=httpx.Response(401, text="bad key")
            )
            with pytest.raises(ProviderError) as exc_info:
                await _call(provider)
        assert exc_info.value.provider_http_status == 401

    @pytest.mark.asyncio(loop_scope="function")
    async def test_connect_error_raises_provider_error_with_none_status(self) -> None:
        provider = AnthropicLLMProvider(_settings())
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            mock.post("/v1/messages").mock(side_effect=httpx.ConnectError("down"))
            with pytest.raises(ProviderError) as exc_info:
                await _call(provider)
        assert exc_info.value.provider_http_status is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_read_timeout_raises_provider_error(self) -> None:
        provider = AnthropicLLMProvider(_settings())
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            mock.post("/v1/messages").mock(side_effect=httpx.ReadTimeout("slow"))
            with pytest.raises(ProviderError) as exc_info:
                await _call(provider)
        assert exc_info.value.provider_http_status is None


# ---------------------------------------------------------------------------
# Response-shape PolicyErrors
# ---------------------------------------------------------------------------


class TestPolicyErrors:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_zero_tool_use_blocks_raises_policy_error(self) -> None:
        provider = AnthropicLLMProvider(_settings())
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            mock.post("/v1/messages").mock(
                return_value=httpx.Response(
                    200,
                    json=_base_response(content=[{"type": "text", "text": "no tool"}]),
                )
            )
            with pytest.raises(PolicyError, match="policy selected no tool"):
                await _call(provider)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_max_tokens_stop_reason_includes_hint(self) -> None:
        provider = AnthropicLLMProvider(_settings())
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            mock.post("/v1/messages").mock(
                return_value=httpx.Response(
                    200,
                    json=_base_response(
                        content=[{"type": "text", "text": "truncated"}],
                        stop_reason="max_tokens",
                    ),
                )
            )
            with pytest.raises(PolicyError, match="max_tokens"):
                await _call(provider)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_multiple_tool_use_blocks_raises_policy_error(self) -> None:
        provider = AnthropicLLMProvider(_settings())
        payload = _base_response(
            content=[
                {"type": "tool_use", "id": "tu_1", "name": "analyze_brief", "input": {}},
                {"type": "tool_use", "id": "tu_2", "name": "draft_plan", "input": {}},
            ]
        )
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            mock.post("/v1/messages").mock(
                return_value=httpx.Response(200, json=payload)
            )
            with pytest.raises(PolicyError, match="multiple tools") as exc_info:
                await _call(provider)
        assert "analyze_brief" in str(exc_info.value)
        assert "draft_plan" in str(exc_info.value)
