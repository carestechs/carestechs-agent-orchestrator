"""Integration tests for engine failure → error stop (T-056)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.exceptions import EngineError
from app.modules.ai.enums import RunStatus, StepStatus, StopReason
from app.modules.ai.models import Step
from tests.conftest import API_KEY

from .env import integration_env, poll_until_terminal, prepare_agents_dir


@pytest.mark.asyncio(loop_scope="function")
async def test_engine_502_ends_run_as_error(
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
        policy_script=[("analyze_brief", {"brief": "hi"})],
        webhook_signer=webhook_signer,
        api_key=API_KEY,
        fail_on_step_number=1,
        fail_with=EngineError(
            "engine returned 502",
            engine_http_status=502,
            engine_correlation_id="corr-502",
            original_body='{"detail":"bad gateway"}',
        ),
    ) as env:
        resp = await env.client.post(
            "/api/v1/runs",
            json={"agentRef": "sample-linear@1.0", "intake": {"brief": "hi"}},
            headers=env.auth_headers,
        )
        assert resp.status_code == 202
        run_id = uuid.UUID(resp.json()["data"]["id"])
        env.run_ids.append(run_id)

        run = await poll_until_terminal(env, run_id, timeout_seconds=5.0)
        assert run.status == RunStatus.FAILED
        assert run.stop_reason == StopReason.ERROR

        async with env.session_factory() as session:
            steps = list(
                (
                    await session.execute(
                        select(Step).where(Step.run_id == run_id)
                    )
                ).scalars()
            )
        assert len(steps) == 1
        step = steps[0]
        assert step.status == StepStatus.FAILED
        assert step.error is not None
        assert step.error["engine_http_status"] == 502
        assert step.error["engine_correlation_id"] == "corr-502"
        assert step.error["original_body"] == '{"detail":"bad gateway"}'


@pytest.mark.asyncio(loop_scope="function")
async def test_engine_connection_error_ends_run_as_error(
    engine: AsyncEngine,
    tmp_path: Path,
    webhook_signer: Callable[[bytes], str],
) -> None:
    """A transport-level failure (no HTTP status) still terminates cleanly."""
    agents_dir = prepare_agents_dir(tmp_path / "agents")
    trace_dir = tmp_path / "trace"

    # We raise EngineError with http_status=None — the real FlowEngineClient
    # wraps httpx.ConnectError into this exact shape before the run_loop
    # ever sees it.  Using EngineError here keeps the test isolated from
    # httpx internals while preserving the on-wire invariants.
    async with integration_env(
        engine,
        agents_dir=agents_dir,
        trace_dir=trace_dir,
        policy_script=[("analyze_brief", {"brief": "hi"})],
        webhook_signer=webhook_signer,
        api_key=API_KEY,
        fail_on_step_number=1,
        fail_with=EngineError(
            "Flow engine request failed: connection refused",
            engine_http_status=None,
            engine_correlation_id=None,
            original_body=None,
        ),
    ) as env:
        resp = await env.client.post(
            "/api/v1/runs",
            json={"agentRef": "sample-linear@1.0", "intake": {"brief": "hi"}},
            headers=env.auth_headers,
        )
        run_id = uuid.UUID(resp.json()["data"]["id"])
        env.run_ids.append(run_id)

        run = await poll_until_terminal(env, run_id, timeout_seconds=5.0)
        assert run.status == RunStatus.FAILED
        assert run.stop_reason == StopReason.ERROR

        async with env.session_factory() as session:
            step = await session.scalar(
                select(Step).where(Step.run_id == run_id)
            )
        assert step is not None
        assert step.error is not None
        assert step.error["engine_http_status"] is None


# Silence unused-import warning — ``httpx`` is kept as a reference for future
# contract tests that want to assert on raw transport exceptions.
_ = httpx
