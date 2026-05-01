"""Tests for FlowEngineLifecycleClient (FEAT-006 rc2 / T-128)."""

from __future__ import annotations

import uuid

import pytest
import respx
from httpx import Response

from app.core.exceptions import EngineError
from app.modules.ai.lifecycle.engine_client import (
    FlowEngineLifecycleClient,
)

_ASYNC = pytest.mark.asyncio(loop_scope="function")


_BASE = "http://engine.test"
_API_KEY = "test-api-key"
_TOKEN_RESP = {
    "data": {
        "accessToken": "jwt-xxx",
        "expiresAt": "2099-01-01T00:00:00Z",
        "tokenType": "Bearer",
    }
}


async def _make_client() -> FlowEngineLifecycleClient:
    return FlowEngineLifecycleClient(base_url=_BASE, api_key=_API_KEY, max_retries=3)


@_ASYNC
class TestAuth:
    async def test_first_call_fetches_token(self) -> None:
        with respx.mock(base_url=_BASE, assert_all_mocked=False) as rx:
            token_route = rx.post("/api/auth/token").mock(
                return_value=Response(200, json=_TOKEN_RESP)
            )
            rx.get("/api/workflows").mock(
                return_value=Response(200, json={"data": []})
            )
            client = await _make_client()
            await client.get_workflow_by_name("anything")
            await client.aclose()
            assert token_route.call_count == 1

    async def test_401_triggers_reauth(self) -> None:
        with respx.mock(base_url=_BASE, assert_all_mocked=False) as rx:
            token_route = rx.post("/api/auth/token").mock(
                return_value=Response(200, json=_TOKEN_RESP)
            )
            route = rx.get("/api/workflows")
            route.side_effect = [
                Response(401, json={"title": "Unauthorized"}),
                Response(200, json={"data": []}),
            ]
            client = await _make_client()
            result = await client.get_workflow_by_name("x")
            await client.aclose()
            assert result is None
            assert token_route.call_count == 2
            assert route.call_count == 2


@_ASYNC
class TestRetry:
    async def test_5xx_retries_then_succeeds(self) -> None:
        with respx.mock(base_url=_BASE, assert_all_mocked=False) as rx:
            rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
            route = rx.get("/api/workflows")
            route.side_effect = [
                Response(502, text="bad gateway"),
                Response(200, json={"data": []}),
            ]
            client = await _make_client()
            await client.get_workflow_by_name("x")
            await client.aclose()
            assert route.call_count == 2

    async def test_5xx_exhausted_raises(self) -> None:
        with respx.mock(base_url=_BASE, assert_all_mocked=False) as rx:
            rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
            rx.get("/api/workflows").mock(
                return_value=Response(503, text="still bad")
            )
            client = await _make_client()
            with pytest.raises(EngineError):
                await client.get_workflow_by_name("x")
            await client.aclose()


@_ASYNC
class TestCreateWorkflow:
    async def test_happy(self) -> None:
        wf_id = str(uuid.uuid4())
        with respx.mock(base_url=_BASE, assert_all_mocked=False) as rx:
            rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
            rx.post("/api/workflows").mock(
                return_value=Response(201, json={"data": {"id": wf_id}})
            )
            client = await _make_client()
            result = await client.create_workflow(
                name="test",
                statuses=[{"name": "a", "position": 0, "isTerminal": False}],
                transitions=[],
                initial_status="a",
            )
            await client.aclose()
            assert str(result) == wf_id

    async def test_conflict_raises(self) -> None:
        with respx.mock(base_url=_BASE, assert_all_mocked=False) as rx:
            rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
            rx.post("/api/workflows").mock(
                return_value=Response(409, json={"title": "Conflict"})
            )
            client = await _make_client()
            with pytest.raises(EngineError) as ex:
                await client.create_workflow(
                    name="dupe",
                    statuses=[],
                    transitions=[],
                    initial_status="x",
                )
            await client.aclose()
            assert ex.value.engine_http_status == 409


@_ASYNC
class TestTransitionItem:
    async def test_sends_correlation_id_as_structured_field(self) -> None:
        import json as _json

        item_id = uuid.uuid4()
        corr = uuid.uuid4()
        with respx.mock(base_url=_BASE, assert_all_mocked=False) as rx:
            rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
            route = rx.post(f"/api/items/{item_id}/transitions").mock(
                return_value=Response(200, json={"data": {}})
            )
            client = await _make_client()
            await client.transition_item(
                item_id=item_id,
                to_status="approved",
                correlation_id=corr,
                actor="admin",
            )
            await client.aclose()
            sent = _json.loads(route.calls[0].request.content.decode())
            # correlationId is a top-level structured field — the engine
            # echoes it back on the resulting webhook.  No "orchestrator-corr:"
            # in the comment any more.
            assert sent["correlationId"] == str(corr)
            assert "orchestrator-corr" not in sent.get("comment", "")
            assert "admin" in sent.get("comment", "")

    async def test_422_surfaces_detail(self) -> None:
        item_id = uuid.uuid4()
        with respx.mock(base_url=_BASE, assert_all_mocked=False) as rx:
            rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
            rx.post(f"/api/items/{item_id}/transitions").mock(
                return_value=Response(
                    422, text="Transition from draft to deployed not allowed"
                )
            )
            client = await _make_client()
            with pytest.raises(EngineError) as ex:
                await client.transition_item(
                    item_id=item_id,
                    to_status="deployed",
                    correlation_id=uuid.uuid4(),
                )
            await client.aclose()
            assert "not allowed" in ex.value.original_body  # type: ignore[operator]


# ``extract_correlation_id`` was the legacy helper that parsed
# ``orchestrator-corr:<uuid>`` out of the engine's ``triggered_by``
# field on the assumption that the engine echoed the transition's
# comment.  It does not — ``triggered_by`` is the engine's actor id.
# The function and its tests were removed when the orchestrator
# switched to sending ``correlationId`` as a structured request field
# (echoed back as ``correlationId`` on the webhook).
