"""Live contract test for engine dispatch.

Skipped by default — opt in with ``--run-live`` AND a reachable engine at
``ENGINE_LIVE_URL``.  Exists so a scheduled CI job can validate our
client against the real flow engine without forcing it into fast CI.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.config import Settings
from app.modules.ai.engine_client import FlowEngineClient


def _skip_if_no_live() -> None:
    if not os.environ.get("ENGINE_LIVE_URL"):
        pytest.skip("set ENGINE_LIVE_URL to run the live engine contract test")


@pytest.mark.live
@pytest.mark.asyncio(loop_scope="function")
async def test_dispatch_node_roundtrip_against_live_engine() -> None:
    """Happy-path round-trip: POST a dispatch, receive an engineRunId."""
    _skip_if_no_live()

    settings = Settings(
        database_url="postgresql+asyncpg://u:p@localhost:5432/unused",  # type: ignore[arg-type]
        orchestrator_api_key="unused",  # type: ignore[arg-type]
        engine_webhook_secret="unused",  # type: ignore[arg-type]
        engine_base_url=os.environ["ENGINE_LIVE_URL"],  # type: ignore[arg-type]
        engine_api_key=os.environ.get("ENGINE_LIVE_API_KEY"),  # type: ignore[arg-type]
        public_base_url="http://localhost:8000",  # type: ignore[arg-type]
    )
    client = FlowEngineClient(settings)

    try:
        engine_run_id = await client.dispatch_node(
            run_id=uuid.uuid4(),
            step_id=uuid.uuid4(),
            agent_ref="contract-smoke@1.0",
            node_name="noop",
            node_inputs={},
        )
        assert engine_run_id
    finally:
        await client.aclose()
