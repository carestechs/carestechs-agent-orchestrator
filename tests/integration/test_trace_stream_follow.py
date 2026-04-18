"""End-to-end follow-mode streaming test (T-084 / AC-2).

Opens ``GET /runs/{id}/trace?follow=true`` *before* the run terminates,
collects every NDJSON line, and verifies the stream closes cleanly once
the run reaches terminal state.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.ai.enums import RunStatus
from tests.conftest import API_KEY

from .env import integration_env, poll_until_terminal, prepare_agents_dir


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.usefixtures("fresh_pool", "fast_tail_poll")
async def test_follow_stream_captures_live_run(
    engine: AsyncEngine,
    tmp_path: Path,
    webhook_signer: Callable[[bytes], str],
) -> None:
    agents_dir = prepare_agents_dir(tmp_path / "agents")
    trace_dir = tmp_path / "trace"

    async with integration_env(
        engine,
        agents_dir=agents_dir,
        trace_dir=trace_dir,
        policy_script=[
            ("analyze_brief", {"brief": "hi"}),
            ("draft_plan", {}),
            ("review_plan", {}),
        ],
        webhook_signer=webhook_signer,
        api_key=API_KEY,
        engine_delay_seconds=0.05,  # run takes a few hundred ms overall
    ) as env:
        # Start the run.
        resp = await env.client.post(
            "/api/v1/runs",
            json={"agentRef": "sample-linear@1.0", "intake": {"brief": "hi"}},
            headers=env.auth_headers,
        )
        assert resp.status_code == 202
        run_id = uuid.UUID(resp.json()["data"]["id"])
        env.run_ids.append(run_id)

        collected: list[dict[str, Any]] = []

        # Open the follow stream immediately — before the run terminates.
        # The stream iterator will close on its own once the service sees
        # a terminal Run.status plus two quiet polls.
        async with env.client.stream(
            "GET",
            f"/api/v1/runs/{run_id}/trace",
            params={"follow": "true"},
            headers=env.auth_headers,
            timeout=10.0,
        ) as resp:
            assert resp.status_code == 200
            async for raw in resp.aiter_lines():
                if not raw:
                    continue
                collected.append(json.loads(raw))

        run = await poll_until_terminal(env, run_id, timeout_seconds=2.0)
        assert run.status == RunStatus.COMPLETED

        kinds = [record["kind"] for record in collected]
        assert kinds.count("step") >= 3
        assert kinds.count("policy_call") >= 3
        assert kinds.count("webhook_event") >= 3
