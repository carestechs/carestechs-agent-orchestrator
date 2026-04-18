"""Factory dispatch + redaction tests for the Anthropic wiring (T-069)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from app.config import Settings
from app.core.exceptions import ProviderError
from app.core.llm import StubLLMProvider, ToolDefinition, get_llm_provider
from app.core.llm_anthropic import AnthropicLLMProvider

_BASE_KW: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "orchestrator_api_key": "k",
    "engine_webhook_secret": "s",
    "engine_base_url": "http://engine.test",
}


def _anthropic_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        **_BASE_KW,
        "llm_provider": "anthropic",
        "anthropic_api_key": "sk-ant-test-xxx",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _stub_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {**_BASE_KW, "llm_provider": "stub"}
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _tool_use_response(name: str = "echo") -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-4-7",
        "content": [
            {"type": "tool_use", "id": "tu_1", "name": name, "input": {"ok": True}}
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 5, "output_tokens": 1},
    }


class TestFactoryDispatch:
    def test_factory_returns_anthropic_provider_when_selected(self) -> None:
        provider = get_llm_provider(_anthropic_settings())
        assert isinstance(provider, AnthropicLLMProvider)

    def test_factory_returns_stub_provider_when_selected(self) -> None:
        provider = get_llm_provider(_stub_settings())
        assert isinstance(provider, StubLLMProvider)

    def test_unknown_provider_raises_provider_error(self) -> None:
        fake = SimpleNamespace(llm_provider="made-up")
        with pytest.raises(ProviderError, match="unknown llm_provider"):
            get_llm_provider(fake)


class TestApiKeyRedaction:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_raw_response_does_not_contain_api_key(self) -> None:
        secret = "sk-ant-SECRET_MARKER_abcdef1234567890"
        provider = AnthropicLLMProvider(
            _anthropic_settings(anthropic_api_key=secret)
        )
        tool = ToolDefinition(
            name="echo",
            description="e",
            parameters={"type": "object", "properties": {}},
        )
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            mock.post("/v1/messages").mock(
                return_value=httpx.Response(200, json=_tool_use_response())
            )
            result = await provider.chat_with_tools(
                system="SYS",
                messages=[{"role": "user", "content": "hi"}],
                tools=[tool],
            )
        assert result.raw_response is not None
        dumped = json.dumps(result.raw_response)
        assert "SECRET_MARKER" not in dumped
        assert "sk-ant-" not in dumped
