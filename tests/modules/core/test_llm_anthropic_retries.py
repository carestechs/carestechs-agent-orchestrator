"""Bounded-retry tests for AnthropicLLMProvider (T-068).

These tests re-enable the real backoff constants inside each test body (the
directory-level ``conftest.py`` otherwise zeroes them for speed) and seed
the provider's RNG so jitter is deterministic.
"""

from __future__ import annotations

import random
import time
from typing import Any

import httpx
import pytest
import respx

from app.config import Settings
from app.core.exceptions import ProviderError
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


def _build() -> AnthropicLLMProvider:
    provider = AnthropicLLMProvider(_settings())
    provider._rng = random.Random(42)  # deterministic jitter
    return provider


async def _call(provider: AnthropicLLMProvider) -> Any:
    return await provider.chat_with_tools(
        system="SYS",
        messages=[{"role": "user", "content": "hi"}],
        tools=[_TOOL],
    )


def _ok_response() -> dict[str, Any]:
    return {
        "id": "msg_ok",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-4-7",
        "content": [
            {"type": "tool_use", "id": "tu_ok", "name": "analyze_brief", "input": {}}
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 5, "output_tokens": 1},
    }


# ---------------------------------------------------------------------------
# Retry counting
# ---------------------------------------------------------------------------


class TestRetryCount:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_three_consecutive_5xx_exhaust_retries_and_raise(self) -> None:
        provider = _build()
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            route = mock.post("/v1/messages").mock(
                return_value=httpx.Response(500, text="boom")
            )
            with pytest.raises(ProviderError):
                await _call(provider)
        assert route.call_count == 3

    @pytest.mark.asyncio(loop_scope="function")
    async def test_retry_succeeds_on_second_attempt(self) -> None:
        provider = _build()
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            route = mock.post("/v1/messages").mock(
                side_effect=[
                    httpx.Response(500, text="boom"),
                    httpx.Response(200, json=_ok_response()),
                ]
            )
            result = await _call(provider)
        assert route.call_count == 2
        assert result.name == "analyze_brief"
        assert result.usage.latency_ms >= 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_429_retries(self) -> None:
        provider = _build()
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            route = mock.post("/v1/messages").mock(
                side_effect=[
                    httpx.Response(429, text="rate limited"),
                    httpx.Response(200, json=_ok_response()),
                ]
            )
            result = await _call(provider)
        assert route.call_count == 2
        assert result.name == "analyze_brief"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_connection_error_retries(self) -> None:
        provider = _build()
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            route = mock.post("/v1/messages").mock(
                side_effect=[
                    httpx.ConnectError("down"),
                    httpx.Response(200, json=_ok_response()),
                ]
            )
            result = await _call(provider)
        assert route.call_count == 2
        assert result.name == "analyze_brief"


# ---------------------------------------------------------------------------
# No-retry on non-transient errors
# ---------------------------------------------------------------------------


class TestNoRetryOnNonTransient:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_400_does_not_retry(self) -> None:
        provider = _build()
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            route = mock.post("/v1/messages").mock(
                return_value=httpx.Response(400, text="bad request")
            )
            with pytest.raises(ProviderError):
                await _call(provider)
        assert route.call_count == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_401_does_not_retry(self) -> None:
        provider = _build()
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            route = mock.post("/v1/messages").mock(
                return_value=httpx.Response(401, text="unauth")
            )
            with pytest.raises(ProviderError):
                await _call(provider)
        assert route.call_count == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_403_does_not_retry(self) -> None:
        provider = _build()
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            route = mock.post("/v1/messages").mock(
                return_value=httpx.Response(403, text="forbidden")
            )
            with pytest.raises(ProviderError):
                await _call(provider)
        assert route.call_count == 1


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


class TestObservability:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_warning_logged_on_each_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Retry emits a WARNING per attempt.

        Directly spies on the provider module's ``logger.warning`` call
        — the pytest ``caplog`` fixture and the standard logging
        infrastructure are both unreliable here because other tests in the
        suite reconfigure the root logger (see ``app.core.logging``).
        """
        import app.core.llm_anthropic as provider_module

        calls: list[tuple[str, dict[str, Any]]] = []

        def _spy_warning(msg: str, *args: Any, **kwargs: Any) -> None:
            calls.append((msg, kwargs.get("extra", {})))

        monkeypatch.setattr(provider_module.logger, "warning", _spy_warning)

        provider = _build()
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            mock.post("/v1/messages").mock(
                side_effect=[
                    httpx.Response(500, text="boom-1"),
                    httpx.Response(500, text="boom-2"),
                    httpx.Response(200, json=_ok_response()),
                ]
            )
            await _call(provider)

        retries = [c for c in calls if c[0] == "anthropic retry"]
        assert len(retries) == 2
        # Each retry record carries the expected extras.
        for _, extra in retries:
            assert "attempt" in extra
            assert "backoff_s" in extra

    @pytest.mark.asyncio(loop_scope="function")
    async def test_total_backoff_bounded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Three-failure run completes within ~6.5s even with real backoff."""
        # Restore the real backoff constants for this test only — the
        # directory-level conftest zeroed them, so opt back in.
        monkeypatch.setattr("app.core.llm_anthropic._BACKOFF_BASE_SECONDS", 0.5)
        monkeypatch.setattr("app.core.llm_anthropic._JITTER_SECONDS", 0.05)

        provider = _build()
        with respx.mock(base_url="https://api.anthropic.com") as mock:
            mock.post("/v1/messages").mock(
                return_value=httpx.Response(500, text="boom")
            )
            start = time.perf_counter()
            with pytest.raises(ProviderError):
                await _call(provider)
            elapsed = time.perf_counter() - start
        # Budget: 2 sleeps of (0.5 + 1.0) ≈ 1.5s, plus minimal API time.
        # Upper bound with generous margin.
        assert elapsed < 6.5, f"retry loop took {elapsed:.2f}s"
