"""Tests for FlowEngineClient — the typed httpx wrapper for the flow engine."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.config import Settings
from app.core.exceptions import EngineError
from app.modules.ai.engine_client import FlowEngineClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ENGINE_BASE_URL = "https://engine.test"


def _make_settings(**overrides: object) -> Settings:
    """Return a ``Settings`` instance pointing at the fake engine."""
    defaults: dict[str, object] = {
        "database_url": "postgresql+asyncpg://u:p@localhost:5432/test",
        "orchestrator_api_key": "test-key",
        "engine_webhook_secret": "webhook-secret",
        "engine_base_url": ENGINE_BASE_URL,
        "engine_api_key": "engine-key",
        "llm_provider": "stub",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def settings() -> Settings:
    return _make_settings()


@pytest.fixture
def client(settings: Settings) -> FlowEngineClient:
    return FlowEngineClient(settings)


# ---------------------------------------------------------------------------
# health() — success
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_health_returns_true_on_2xx(client: FlowEngineClient) -> None:
    respx.get(f"{ENGINE_BASE_URL}/health").mock(
        return_value=httpx.Response(200, json={"status": "ok"}),
    )
    result = await client.health()
    assert result is True


# ---------------------------------------------------------------------------
# health() — non-2xx
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_health_returns_false_on_5xx(client: FlowEngineClient) -> None:
    respx.get(f"{ENGINE_BASE_URL}/health").mock(
        return_value=httpx.Response(500, text="Internal Server Error"),
    )
    result = await client.health()
    assert result is False


@respx.mock
@pytest.mark.asyncio
async def test_health_returns_false_on_4xx(client: FlowEngineClient) -> None:
    respx.get(f"{ENGINE_BASE_URL}/health").mock(
        return_value=httpx.Response(403, text="Forbidden"),
    )
    result = await client.health()
    assert result is False


# ---------------------------------------------------------------------------
# health() — connection error
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_health_returns_false_on_connection_error(
    client: FlowEngineClient,
) -> None:
    respx.get(f"{ENGINE_BASE_URL}/health").mock(side_effect=httpx.ConnectError("refused"))
    result = await client.health()
    assert result is False


# dispatch_node has its own dedicated tests in test_engine_client_dispatch.py (T-036).


# ---------------------------------------------------------------------------
# _request — wraps HTTPStatusError
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_request_wraps_http_status_error(client: FlowEngineClient) -> None:
    respx.post(f"{ENGINE_BASE_URL}/some-endpoint").mock(
        return_value=httpx.Response(
            422,
            text='{"detail":"bad"}',
            headers={"x-correlation-id": "corr-42"},
        ),
    )
    with pytest.raises(EngineError) as exc_info:
        await client._request("POST", "/some-endpoint")

    err = exc_info.value
    assert err.engine_http_status == 422
    assert err.engine_correlation_id == "corr-42"
    assert err.original_body == '{"detail":"bad"}'


# ---------------------------------------------------------------------------
# _request — wraps RequestError (connection-level)
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_request_wraps_connection_error(client: FlowEngineClient) -> None:
    respx.get(f"{ENGINE_BASE_URL}/fail").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(EngineError) as exc_info:
        await client._request("GET", "/fail")

    err = exc_info.value
    assert err.engine_http_status is None
    assert err.engine_correlation_id is None
    assert err.original_body is None
    assert "refused" in err.detail


# ---------------------------------------------------------------------------
# Auth header is set from settings
# ---------------------------------------------------------------------------


def test_auth_header_set(settings: Settings) -> None:
    c = FlowEngineClient(settings)
    assert c._client.headers.get("authorization") == "Bearer engine-key"


def test_auth_header_absent_when_no_key() -> None:
    s = _make_settings(engine_api_key=None)
    c = FlowEngineClient(s)
    assert "authorization" not in c._client.headers


# ---------------------------------------------------------------------------
# _request — no correlation header
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_request_wraps_error_without_correlation_id(
    client: FlowEngineClient,
) -> None:
    respx.post(f"{ENGINE_BASE_URL}/no-corr").mock(
        return_value=httpx.Response(500, text="oops"),
    )
    with pytest.raises(EngineError) as exc_info:
        await client._request("POST", "/no-corr")

    err = exc_info.value
    assert err.engine_http_status == 500
    assert err.engine_correlation_id is None
    assert err.original_body == "oops"
