"""Integration tests for mid-flight cancel (T-055)."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.ai.enums import RunStatus, StepStatus, StopReason
from app.modules.ai.models import Step, WebhookEvent
from tests.conftest import API_KEY

from .env import integration_env, poll_until_terminal, prepare_agents_dir


@pytest.mark.asyncio(loop_scope="function")
async def test_cancel_during_in_flight_step_terminates_run(
    engine: AsyncEngine,
    tmp_path: Path,
    webhook_signer: Callable[[bytes], str],
) -> None:
    """Run starts, dispatches step 1, webhook is delayed, operator cancels.

    Expected: run transitions to ``cancelled`` within the local 500 ms
    target (CI bound 2 s documented).  Late webhook (when it eventually
    fires) is handled gracefully by reconciliation but does not re-open
    the terminal run.
    """
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
        engine_delay_seconds=2.0,  # webhook for step 1 arrives 2s later
    ) as env:
        resp = await env.client.post(
            "/api/v1/runs",
            json={"agentRef": "sample-linear@1.0", "intake": {"brief": "hi"}},
            headers=env.auth_headers,
        )
        assert resp.status_code == 202
        run_id = uuid.UUID(resp.json()["data"]["id"])
        env.run_ids.append(run_id)

        # Wait until the first dispatch actually happens so we know we're mid-flight.
        for _ in range(100):
            if env.engine_echo.dispatches:
                break
            import asyncio

            await asyncio.sleep(0.02)
        assert env.engine_echo.dispatches, "engine never received the first dispatch"

        # Cancel.
        t_cancel = time.perf_counter()
        resp = await env.client.post(
            f"/api/v1/runs/{run_id}/cancel",
            json={"reason": "operator abort"},
            headers=env.auth_headers,
        )
        assert resp.status_code == 200, resp.text

        run = await poll_until_terminal(env, run_id, timeout_seconds=3.0)
        elapsed = time.perf_counter() - t_cancel

        assert run.status == RunStatus.CANCELLED
        assert run.stop_reason == StopReason.CANCELLED
        assert run.final_state is not None
        assert run.final_state.get("cancel_reason") == "operator abort"
        # Local target: 500 ms; CI bound generous.
        assert elapsed < 2.0, f"cancel turnaround was {elapsed:.2f}s"

        async with env.session_factory() as session:
            steps = list(
                (
                    await session.execute(
                        select(Step).where(Step.run_id == run_id)
                    )
                ).scalars()
            )
        # Step 1 was dispatched but never got a completion webhook before cancel.
        assert len(steps) == 1
        assert steps[0].status in {StepStatus.DISPATCHED, StepStatus.FAILED}


@pytest.mark.asyncio(loop_scope="function")
async def test_late_webhook_after_cancel_is_persisted_but_no_mutation(
    engine: AsyncEngine,
    tmp_path: Path,
    webhook_signer: Callable[[bytes], str],
) -> None:
    """Cancel a run, then POST a valid webhook for the in-flight step.

    Expected: the webhook is persisted (forensics), returns 202, but the
    step is NOT advanced because the run is already cancelled.
    """
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
        engine_delay_seconds=10.0,  # webhook effectively never fires inside the test
    ) as env:
        resp = await env.client.post(
            "/api/v1/runs",
            json={"agentRef": "sample-linear@1.0", "intake": {"brief": "hi"}},
            headers=env.auth_headers,
        )
        run_id = uuid.UUID(resp.json()["data"]["id"])
        env.run_ids.append(run_id)

        # Wait for dispatch.
        for _ in range(100):
            if env.engine_echo.dispatches:
                break
            import asyncio

            await asyncio.sleep(0.02)
        assert env.engine_echo.dispatches

        # Cancel.
        await env.client.post(
            f"/api/v1/runs/{run_id}/cancel",
            json={"reason": "abort"},
            headers=env.auth_headers,
        )
        await poll_until_terminal(env, run_id, timeout_seconds=3.0)

        # Grab the step's engine_run_id so we can forge the webhook.
        async with env.session_factory() as session:
            step = await session.scalar(
                select(Step).where(Step.run_id == run_id)
            )
        assert step is not None
        assert step.engine_run_id is not None
        step_status_before = StepStatus(step.status)

        # Now POST a well-formed, correctly-signed webhook for that step.
        payload: dict[str, object] = {
            "eventType": "node_finished",
            "engineRunId": step.engine_run_id,
            "engineEventId": f"late-{uuid.uuid4()}",
            "occurredAt": datetime.now(UTC).isoformat(),
            "payload": {"result": {"late": True}},
        }
        body = json.dumps(payload).encode("utf-8")
        resp = await env.client.post(
            "/hooks/engine/events",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Engine-Signature": webhook_signer(body),
            },
        )
        assert resp.status_code == 202, resp.text

        # Step status is unchanged; webhook row persisted.
        async with env.session_factory() as session:
            step_after = await session.scalar(
                select(Step).where(Step.id == step.id)
            )
            events = list(
                (
                    await session.execute(
                        select(WebhookEvent).where(WebhookEvent.run_id == run_id)
                    )
                ).scalars()
            )
        assert step_after is not None
        assert StepStatus(step_after.status) == step_status_before
        # At least the late event is stored (EngineEcho's scheduled task may or
        # may not have delivered anything before the test block exited; what we
        # care about is that OUR direct POST produced a row).
        assert any(e.dedupe_key.startswith("late-") for e in events)
