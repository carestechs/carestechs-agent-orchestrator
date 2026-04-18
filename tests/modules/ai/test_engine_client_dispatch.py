"""Tests for ``FlowEngineClient.dispatch_node`` (T-036)."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
import respx

from app.config import Settings
from app.core.exceptions import EngineError
from app.modules.ai.engine_client import FlowEngineClient

_API_KEY = "engine-secret"


def _settings(with_auth: bool = True) -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://u:p@localhost:5432/testdb",  # type: ignore[arg-type]
        orchestrator_api_key="k",  # type: ignore[arg-type]
        engine_webhook_secret="s",  # type: ignore[arg-type]
        engine_base_url="http://engine.test",  # type: ignore[arg-type]
        engine_api_key=_API_KEY if with_auth else None,  # type: ignore[arg-type]
        public_base_url="http://orch.test",  # type: ignore[arg-type]
        engine_dispatch_timeout_seconds=2,
    )


async def _dispatch(client: FlowEngineClient) -> str:
    return await client.dispatch_node(
        run_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        step_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        agent_ref="demo@1.0",
        node_name="analyze_brief",
        node_inputs={"brief": "hi"},
    )


# ---------------------------------------------------------------------------
# Happy path + payload shape
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_engine_run_id(self) -> None:
        client = FlowEngineClient(_settings())
        with respx.mock(base_url="http://engine.test") as mock:
            route = mock.post("/nodes/dispatch").mock(
                return_value=httpx.Response(200, json={"engineRunId": "eng-123"})
            )
            result = await _dispatch(client)
        await client.aclose()

        assert result == "eng-123"
        assert route.called

    @pytest.mark.asyncio(loop_scope="function")
    async def test_outbound_payload_shape(self) -> None:
        client = FlowEngineClient(_settings())
        with respx.mock(base_url="http://engine.test") as mock:
            route = mock.post("/nodes/dispatch").mock(
                return_value=httpx.Response(200, json={"engineRunId": "eng-xyz"})
            )
            await _dispatch(client)
        await client.aclose()

        body = json.loads(route.calls[0].request.content)
        assert body == {
            "agentRef": "demo@1.0",
            "runId": "00000000-0000-0000-0000-000000000001",
            "stepId": "00000000-0000-0000-0000-000000000002",
            "nodeName": "analyze_brief",
            "nodeInputs": {"brief": "hi"},
            "callbackUrl": "http://orch.test/hooks/engine/events",
        }

    @pytest.mark.asyncio(loop_scope="function")
    async def test_auth_header_present_when_configured(self) -> None:
        client = FlowEngineClient(_settings(with_auth=True))
        with respx.mock(base_url="http://engine.test") as mock:
            route = mock.post("/nodes/dispatch").mock(
                return_value=httpx.Response(200, json={"engineRunId": "eng-a"})
            )
            await _dispatch(client)
        await client.aclose()
        assert route.calls[0].request.headers["authorization"] == f"Bearer {_API_KEY}"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_auth_header_absent_when_unconfigured(self) -> None:
        client = FlowEngineClient(_settings(with_auth=False))
        with respx.mock(base_url="http://engine.test") as mock:
            route = mock.post("/nodes/dispatch").mock(
                return_value=httpx.Response(200, json={"engineRunId": "eng-b"})
            )
            await _dispatch(client)
        await client.aclose()
        assert "authorization" not in route.calls[0].request.headers


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrorPaths:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_5xx_wraps_to_engine_error(self) -> None:
        client = FlowEngineClient(_settings())
        with respx.mock(base_url="http://engine.test") as mock:
            mock.post("/nodes/dispatch").mock(
                return_value=httpx.Response(
                    500,
                    text="boom",
                    headers={"x-correlation-id": "corr-500"},
                )
            )
            with pytest.raises(EngineError) as exc_info:
                await _dispatch(client)
        await client.aclose()

        assert exc_info.value.engine_http_status == 500
        assert exc_info.value.engine_correlation_id == "corr-500"
        assert exc_info.value.original_body == "boom"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_4xx_surfaces_correlation_id(self) -> None:
        client = FlowEngineClient(_settings())
        with respx.mock(base_url="http://engine.test") as mock:
            mock.post("/nodes/dispatch").mock(
                return_value=httpx.Response(
                    400, text="bad", headers={"x-correlation-id": "abc-123"}
                )
            )
            with pytest.raises(EngineError) as exc_info:
                await _dispatch(client)
        await client.aclose()
        assert exc_info.value.engine_correlation_id == "abc-123"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_connection_error_wraps(self) -> None:
        client = FlowEngineClient(_settings())
        with respx.mock(base_url="http://engine.test") as mock:
            mock.post("/nodes/dispatch").mock(side_effect=httpx.ConnectError("down"))
            with pytest.raises(EngineError) as exc_info:
                await _dispatch(client)
        await client.aclose()
        assert exc_info.value.engine_http_status is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_timeout_wraps(self) -> None:
        client = FlowEngineClient(_settings())
        with respx.mock(base_url="http://engine.test") as mock:
            mock.post("/nodes/dispatch").mock(side_effect=httpx.ReadTimeout("slow"))
            with pytest.raises(EngineError) as exc_info:
                await _dispatch(client)
        await client.aclose()
        assert exc_info.value.engine_http_status is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_engine_run_id_raises(self) -> None:
        client = FlowEngineClient(_settings())
        with respx.mock(base_url="http://engine.test") as mock:
            mock.post("/nodes/dispatch").mock(
                return_value=httpx.Response(200, json={"other": "field"})
            )
            with pytest.raises(EngineError, match="missing engineRunId"):
                await _dispatch(client)
        await client.aclose()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_non_json_response_raises(self) -> None:
        client = FlowEngineClient(_settings())
        with respx.mock(base_url="http://engine.test") as mock:
            mock.post("/nodes/dispatch").mock(
                return_value=httpx.Response(200, text="not-json")
            )
            with pytest.raises(EngineError, match="not JSON"):
                await _dispatch(client)
        await client.aclose()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_correlation_header_yields_none(self) -> None:
        """Missing ``x-correlation-id`` header should not crash; surfaces as ``None``."""
        client = FlowEngineClient(_settings())
        with respx.mock(base_url="http://engine.test") as mock:
            mock.post("/nodes/dispatch").mock(
                return_value=httpx.Response(503, text="unavailable")
            )
            with pytest.raises(EngineError) as exc_info:
                await _dispatch(client)
        await client.aclose()
        assert exc_info.value.engine_correlation_id is None
        assert exc_info.value.engine_http_status == 503
        assert exc_info.value.original_body == "unavailable"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_4xx_original_body_preserved(self) -> None:
        client = FlowEngineClient(_settings())
        with respx.mock(base_url="http://engine.test") as mock:
            mock.post("/nodes/dispatch").mock(
                return_value=httpx.Response(
                    422,
                    text='{"error":"invalid node_inputs"}',
                    headers={"x-correlation-id": "v-422"},
                )
            )
            with pytest.raises(EngineError) as exc_info:
                await _dispatch(client)
        await client.aclose()
        assert exc_info.value.engine_http_status == 422
        assert exc_info.value.engine_correlation_id == "v-422"
        assert exc_info.value.original_body == '{"error":"invalid node_inputs"}'


# ---------------------------------------------------------------------------
# Parameterized outcome matrix — condenses the common shape (T-050).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("status", "headers", "body", "expected_http_status", "expected_corr"),
    [
        (400, {"x-correlation-id": "c400"}, "bad-request", 400, "c400"),
        (401, {}, "unauth", 401, None),
        (500, {"x-correlation-id": "c500"}, "boom", 500, "c500"),
        (502, {}, "bad-gateway", 502, None),
    ],
    ids=["400-with-corr", "401-no-corr", "500-with-corr", "502-no-corr"],
)
async def test_status_matrix(
    status: int,
    headers: dict[str, str],
    body: str,
    expected_http_status: int,
    expected_corr: str | None,
) -> None:
    client = FlowEngineClient(_settings())
    with respx.mock(base_url="http://engine.test") as mock:
        mock.post("/nodes/dispatch").mock(
            return_value=httpx.Response(status, text=body, headers=headers)
        )
        with pytest.raises(EngineError) as exc_info:
            await _dispatch(client)
    await client.aclose()

    assert exc_info.value.engine_http_status == expected_http_status
    assert exc_info.value.engine_correlation_id == expected_corr
    assert exc_info.value.original_body == body
