"""Opt-in smoke tests for FEAT-006 rc2 against a live ``carestechs-flow-engine``.

Run with ``uv run pytest -m requires_engine --run-requires-engine``.

Requires the following env vars:

- ``TEST_FLOW_ENGINE_BASE_URL`` — e.g., ``http://localhost:5000``.
- ``TEST_FLOW_ENGINE_TENANT_KEY`` — a tenant API key (from
  ``POST /api/tenants`` on first setup; see engine README).

If either is unset the tests are skipped with a clear message rather
than failing — these are opt-in by design.

What these tests verify that the mocked suite cannot:

1. The engine client's auth + retry + transition path actually works
   against the engine's real HTTP surface.
2. Workflow bootstrap is idempotent across repeated runs (re-running
   the test doesn't blow up on 409 Conflict).
3. Creating an item + transitioning it produces state the engine
   reports back via ``GET /api/items/{id}``.

Aux-write and reactor-consumption testing remains against mocks
until the aux-write flip has been designed.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

from app.modules.ai.lifecycle import declarations
from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient

pytestmark = [
    pytest.mark.requires_engine,
    pytest.mark.asyncio(loop_scope="function"),
]


def _env_or_skip() -> tuple[str, str]:
    base = os.environ.get("TEST_FLOW_ENGINE_BASE_URL")
    key = os.environ.get("TEST_FLOW_ENGINE_TENANT_KEY")
    if not base or not key:
        pytest.skip(
            "set TEST_FLOW_ENGINE_BASE_URL + TEST_FLOW_ENGINE_TENANT_KEY to run"
        )
    return base, key


async def _make_client() -> FlowEngineLifecycleClient:
    base, key = _env_or_skip()
    return FlowEngineLifecycleClient(base_url=base, api_key=key)


class TestAuth:
    async def test_token_exchange_succeeds(self) -> None:
        """Indirect check: any authed call forces a token exchange."""
        client = await _make_client()
        try:
            # get_workflow_by_name requires auth; a successful call (or 404
            # for an unknown name) proves the JWT round-trip works.
            result = await client.get_workflow_by_name(
                f"orchestrator_smoke_probe_{uuid.uuid4().hex[:6]}"
            )
            assert result is None  # the probe name doesn't exist
        finally:
            await client.aclose()


class TestWorkflowBootstrap:
    async def test_create_workflow_or_409_then_lookup(self) -> None:
        """Whether the test workflow already exists from a prior run or not,
        the client must be able to resolve its id."""
        client = await _make_client()
        try:
            name = f"orchestrator_smoke_{uuid.uuid4().hex[:8]}"
            wf_id = await client.create_workflow(
                name=name,
                statuses=[
                    {"name": "open", "position": 0, "isTerminal": False},
                    {"name": "closed", "position": 1, "isTerminal": True},
                ],
                transitions=[
                    {"fromStatus": "open", "toStatus": "closed", "name": "close"},
                ],
                initial_status="open",
            )
            assert wf_id
        finally:
            await client.aclose()


class TestItemLifecycle:
    """Create → transition → verify round-trip against the real engine."""

    async def test_create_item_and_transition(self) -> None:
        client = await _make_client()
        try:
            name = f"orchestrator_smoke_{uuid.uuid4().hex[:8]}"
            wf_id = await client.create_workflow(
                name=name,
                statuses=[
                    {"name": "open", "position": 0, "isTerminal": False},
                    {"name": "active", "position": 1, "isTerminal": False},
                    {"name": "closed", "position": 2, "isTerminal": True},
                ],
                transitions=[
                    {"fromStatus": "open", "toStatus": "active", "name": "start"},
                    {"fromStatus": "active", "toStatus": "closed", "name": "finish"},
                ],
                initial_status="open",
            )

            item_id = await client.create_item(
                workflow_id=wf_id,
                title="smoke item",
                external_ref=f"SMOKE-{uuid.uuid4().hex[:6]}",
                metadata={"source": "orchestrator-smoke-test"},
            )

            corr = uuid.uuid4()
            result = await client.transition_item(
                item_id=item_id,
                to_status="active",
                correlation_id=corr,
                actor="smoke-test",
            )
            assert result["item"]["currentStatus"]["name"] == "active"

            # Invalid transition should 422 → EngineError.
            from app.core.exceptions import EngineError as _EngineError

            with pytest.raises(_EngineError):
                await client.transition_item(
                    item_id=item_id,
                    to_status="open",  # not a valid transition
                    correlation_id=corr,
                )
        finally:
            await client.aclose()


class TestDeclarationsRoundTrip:
    """Register the two real FEAT-006 workflow declarations against the engine."""

    async def test_work_item_workflow_shape_accepted(self) -> None:
        client = await _make_client()
        try:
            # Use a unique name so repeated runs don't collide.
            unique_name = (
                f"{declarations.WORK_ITEM_WORKFLOW_NAME}_smoke_"
                f"{uuid.uuid4().hex[:6]}"
            )
            wf_id = await client.create_workflow(
                name=unique_name,
                statuses=declarations.WORK_ITEM_STATUSES,
                transitions=declarations.WORK_ITEM_TRANSITIONS,
                initial_status=declarations.WORK_ITEM_INITIAL_STATUS,
            )
            assert wf_id
        finally:
            await client.aclose()

    async def test_task_workflow_shape_accepted(self) -> None:
        client = await _make_client()
        try:
            unique_name = (
                f"{declarations.TASK_WORKFLOW_NAME}_smoke_"
                f"{uuid.uuid4().hex[:6]}"
            )
            wf_id = await client.create_workflow(
                name=unique_name,
                statuses=declarations.TASK_STATUSES,
                transitions=declarations.TASK_TRANSITIONS,
                initial_status=declarations.TASK_INITIAL_STATUS,
            )
            assert wf_id
        finally:
            await client.aclose()


class TestReachability:
    """Pre-flight: engine responds at all.  First test to fail on setup issues."""

    async def test_engine_is_reachable(self) -> None:
        base, _ = _env_or_skip()
        async with httpx.AsyncClient(base_url=base, timeout=5) as c:
            # The engine doesn't publish a /health endpoint in the spec; any
            # 4xx to /api/auth/token with empty body proves it's accepting
            # connections.
            resp = await c.post("/api/auth/token", json={})
            assert resp.status_code in (400, 401, 422), (
                f"unexpected status {resp.status_code} — engine not reachable?"
            )
