"""Integration test for the human-executor signal bridge (FEAT-009 / T-217).

A signal POST that lands while a human-mode dispatch is in flight must
deliver to the dispatch's supervisor future *and* keep the legacy
FEAT-005 ``deliver_signal`` path working — pre-FEAT-009 callers see no
change in behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import DispatchMode, DispatchState, RunStatus, StepStatus
from app.modules.ai.models import Dispatch, Run, RunMemory, Step
from app.modules.ai.supervisor import RunSupervisor

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _now() -> datetime:
    return datetime.now(UTC)


async def _seed_run_with_human_dispatch(db: AsyncSession) -> tuple[Run, Dispatch]:
    run = Run(
        agent_ref="lifecycle-agent@0.1.0",
        agent_definition_hash="sha256:" + "0" * 64,
        intake={},
        status=RunStatus.RUNNING,
        started_at=_now(),
        trace_uri="file:///tmp/t.jsonl",
    )
    db.add(run)
    await db.flush()
    db.add(
        RunMemory(
            run_id=run.id,
            data={"tasks": [{"id": "T-001", "title": "demo"}]},
        )
    )
    step = Step(
        run_id=run.id,
        step_number=1,
        node_name="wait_for_implementation",
        node_inputs={},
        status=StepStatus.PENDING,
    )
    db.add(step)
    await db.flush()
    dispatch = Dispatch(
        step_id=step.id,
        run_id=run.id,
        executor_ref="human:wait_for_implementation",
        mode=DispatchMode.HUMAN,
        state=DispatchState.DISPATCHED,
        intake={"task_id": "T-001"},
        dispatched_at=_now(),
    )
    db.add(dispatch)
    await db.commit()
    return run, dispatch


async def test_signal_completes_inflight_human_dispatch(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    app: FastAPI,
) -> None:
    run, dispatch = await _seed_run_with_human_dispatch(db_session)

    from app.core.dependencies import _default_supervisor  # noqa: PLC0415

    sup = _default_supervisor or RunSupervisor()
    app.state.supervisor = sup
    supervisor: RunSupervisor = sup
    supervisor.register_dispatch(run.id, dispatch.dispatch_id)

    body: dict[str, Any] = {
        "name": "implementation-complete",
        "taskId": "T-001",
        "payload": {"prUrl": "https://example.test/pr/1"},
    }
    resp = await client.post(
        f"/api/v1/runs/{run.id}/signals",
        json=body,
        headers=auth_headers,
    )
    assert resp.status_code == 202, resp.text

    # Dispatch transitioned to completed with the signal payload as result.
    await db_session.refresh(dispatch)
    assert dispatch.state == DispatchState.COMPLETED
    assert dispatch.result is not None
    assert dispatch.result["signal_name"] == "implementation-complete"
    assert dispatch.result["task_id"] == "T-001"
    assert dispatch.result["payload"]["prUrl"] == "https://example.test/pr/1"


async def test_signal_without_dispatch_is_legacy_noop(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Pre-FEAT-009 callers (no Dispatch row in flight) keep working."""
    run = Run(
        agent_ref="lifecycle-agent@0.1.0",
        agent_definition_hash="sha256:" + "0" * 64,
        intake={},
        status=RunStatus.RUNNING,
        started_at=_now(),
        trace_uri="file:///tmp/t.jsonl",
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        RunMemory(
            run_id=run.id,
            data={"tasks": [{"id": "T-001", "title": "demo"}]},
        )
    )
    await db_session.commit()

    body = {
        "name": "implementation-complete",
        "taskId": "T-001",
        "payload": {},
    }
    resp = await client.post(
        f"/api/v1/runs/{run.id}/signals",
        json=body,
        headers=auth_headers,
    )
    assert resp.status_code == 202
