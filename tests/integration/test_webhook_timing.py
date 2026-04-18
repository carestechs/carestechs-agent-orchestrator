"""Webhook-driven advancement timing (AC-7, T-057).

Measures the wall-clock gap between the first step's webhook being
persisted and the second policy call's ``created_at`` timestamp.  The
local target is < 100 ms; the CI bound we actually assert is 500 ms to
absorb cold-connection overhead on shared Postgres.  A warm-up run is
executed first so the asyncpg pool is hot by the time we measure.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.ai.enums import RunStatus
from app.modules.ai.models import PolicyCall, WebhookEvent
from tests.conftest import API_KEY

from .env import integration_env, poll_until_terminal, prepare_agents_dir


@pytest.mark.asyncio(loop_scope="function")
async def test_webhook_to_next_policy_call_under_bound(
    engine: AsyncEngine,
    tmp_path: Path,
    webhook_signer: Callable[[bytes], str],
) -> None:
    agents_dir = prepare_agents_dir(tmp_path / "agents")
    trace_dir = tmp_path / "trace"

    # ------------------------------------------------------------------
    # Warmup run — pays the first-connection cost for the asyncpg pool
    # so the measured run's timing reflects steady state.
    # ------------------------------------------------------------------
    async with integration_env(
        engine,
        agents_dir=agents_dir,
        trace_dir=trace_dir,
        policy_script=[("analyze_brief", {"brief": "hi"})],  # terminates via exhaustion
        webhook_signer=webhook_signer,
        api_key=API_KEY,
    ) as warm:
        resp = await warm.client.post(
            "/api/v1/runs",
            json={"agentRef": "sample-linear@1.0", "intake": {"brief": "hi"}},
            headers=warm.auth_headers,
        )
        warm_run_id = uuid.UUID(resp.json()["data"]["id"])
        warm.run_ids.append(warm_run_id)
        await poll_until_terminal(warm, warm_run_id, timeout_seconds=5.0)

    # ------------------------------------------------------------------
    # Measured run — two non-terminal steps so we can measure gap(1 → 2).
    # ------------------------------------------------------------------
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
    ) as env:
        resp = await env.client.post(
            "/api/v1/runs",
            json={"agentRef": "sample-linear@1.0", "intake": {"brief": "hi"}},
            headers=env.auth_headers,
        )
        run_id = uuid.UUID(resp.json()["data"]["id"])
        env.run_ids.append(run_id)

        run = await poll_until_terminal(env, run_id, timeout_seconds=5.0)
        assert run.status == RunStatus.COMPLETED

        async with env.session_factory() as session:
            first_webhook = await session.scalar(
                select(WebhookEvent)
                .where(WebhookEvent.run_id == run_id)
                .order_by(WebhookEvent.received_at.asc())
                .limit(1)
            )
            policy_calls = list(
                (
                    await session.execute(
                        select(PolicyCall)
                        .where(PolicyCall.run_id == run_id)
                        .order_by(PolicyCall.created_at.asc())
                    )
                ).scalars()
            )

        assert first_webhook is not None
        assert len(policy_calls) >= 2
        # Gap between the first webhook arriving and the 2nd policy call firing.
        delta_s = (
            policy_calls[1].created_at - first_webhook.received_at
        ).total_seconds()
        assert 0 <= delta_s < 0.5, (
            f"webhook → next policy call gap was {delta_s * 1000:.0f}ms "
            f"(local target <100ms, CI bound 500ms)"
        )

        # Keep lint happy in case of very fast CI machines.
        await asyncio.sleep(0)
