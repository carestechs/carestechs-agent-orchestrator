"""Construction + protocol-conformance tests for AnthropicLLMProvider (T-065)."""

from __future__ import annotations

from typing import Any

import anthropic
import respx

from app.config import Settings
from app.core.llm import LLMProvider
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


class TestAnthropicLLMProviderConstruction:
    def test_constructor_does_not_hit_the_network(self) -> None:
        with respx.mock(
            base_url="https://api.anthropic.com", assert_all_called=False
        ) as mock:
            route = mock.post("/v1/messages")
            provider = AnthropicLLMProvider(_settings())
            assert isinstance(provider._client, anthropic.AsyncAnthropic)
            assert route.call_count == 0

    def test_protocol_match(self) -> None:
        provider = AnthropicLLMProvider(_settings())
        assert isinstance(provider, LLMProvider)

    def test_model_defaults_to_claude_opus(self) -> None:
        # Settings validator defaults llm_model when the caller leaves it blank.
        provider = AnthropicLLMProvider(_settings())
        assert provider.model == "claude-opus-4-7"

    def test_model_honors_explicit_override(self) -> None:
        provider = AnthropicLLMProvider(_settings(llm_model="claude-sonnet-4-6"))
        assert provider.model == "claude-sonnet-4-6"
