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


# ---------------------------------------------------------------------------
# FEAT-015 / T-296+T-301 — memory_patch_builder hook
# ---------------------------------------------------------------------------


async def _seed_run_with_builder_dispatch(
    db: AsyncSession,
    *,
    agent_ref: str,
    node_name: str,
    task_id: str = "",
) -> tuple[Run, Dispatch]:
    """Seed a run + a paused human dispatch under *agent_ref* / *node_name*.

    Mirrors :func:`_seed_run_with_human_dispatch` but lets the caller
    select the agent ref and node name so the executor-registry lookup
    in ``_deliver_to_human_dispatch`` resolves to a test-supplied binding.
    """
    run = Run(
        agent_ref=agent_ref,
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
        node_name=node_name,
        node_inputs={},
        status=StepStatus.PENDING,
    )
    db.add(step)
    await db.flush()
    dispatch = Dispatch(
        step_id=step.id,
        run_id=run.id,
        executor_ref=f"human:{node_name}",
        mode=DispatchMode.HUMAN,
        state=DispatchState.DISPATCHED,
        intake={"runId": str(run.id), "nodeName": node_name, "taskId": task_id},
        dispatched_at=_now(),
    )
    db.add(dispatch)
    await db.commit()
    return run, dispatch


async def test_builder_hook_embeds_patch_in_envelope(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    app: FastAPI,
) -> None:
    """A binding with a ``memory_patch_builder`` must have its return value
    placed at ``Dispatch.result.__memory_patch`` on signal arrival.
    """
    from app.modules.ai.executors.human import HumanExecutor
    from app.modules.ai.executors.registry import ExecutorRegistry
    from app.modules.ai.supervisor import RunSupervisor

    builder_calls: list[dict[str, Any]] = []

    def builder(payload: dict[str, Any], current_memory: dict[str, Any]) -> dict[str, Any]:
        builder_calls.append(payload)
        return {"plans": {"X": {"plan_markdown": payload.get("plan", "")}}}

    registry = ExecutorRegistry()
    registry.register(
        "test-agent@9.9.9",
        "demo_checkpoint",
        HumanExecutor(
            ref="human:demo_checkpoint",
            expected_signal_name="plan-confirmed",
            memory_patch_builder=builder,
        ),
    )
    app.state.executor_registry = registry

    run, dispatch = await _seed_run_with_builder_dispatch(
        db_session,
        agent_ref="test-agent@9.9.9",
        node_name="demo_checkpoint",
        task_id="T-001",
    )

    sup = RunSupervisor()
    app.state.supervisor = sup
    sup.register_dispatch(run.id, dispatch.dispatch_id)

    body = {
        "name": "plan-confirmed",
        "taskId": "T-001",
        "payload": {"plan": "# Operator plan\n..."},
    }
    resp = await client.post(
        f"/api/v1/runs/{run.id}/signals",
        json=body,
        headers=auth_headers,
    )
    assert resp.status_code == 202, resp.text

    await db_session.refresh(dispatch)
    assert dispatch.state == DispatchState.COMPLETED
    assert dispatch.result is not None
    assert "__memory_patch" in dispatch.result, (
        f"expected __memory_patch in result; got {dispatch.result!r}"
    )
    assert dispatch.result["__memory_patch"] == {
        "plans": {"X": {"plan_markdown": "# Operator plan\n..."}}
    }
    # Builder called exactly once with the operator payload.
    assert len(builder_calls) == 1
    assert builder_calls[0] == {"plan": "# Operator plan\n..."}


async def test_raising_builder_fails_the_dispatch(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    app: FastAPI,
) -> None:
    """A builder that raises must mark the dispatch ``failed`` with the
    exception in ``detail``.  The signal endpoint still returns 202 —
    the run-status flip is what surfaces the failure to operators.
    """
    from app.modules.ai.executors.human import HumanExecutor
    from app.modules.ai.executors.registry import ExecutorRegistry
    from app.modules.ai.supervisor import RunSupervisor

    def failing_builder(payload: dict[str, Any], current_memory: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("operator delivered too early")

    registry = ExecutorRegistry()
    registry.register(
        "test-agent@9.9.9",
        "broken_checkpoint",
        HumanExecutor(
            ref="human:broken_checkpoint",
            expected_signal_name="review-completed",
            memory_patch_builder=failing_builder,
        ),
    )
    app.state.executor_registry = registry

    run, dispatch = await _seed_run_with_builder_dispatch(
        db_session,
        agent_ref="test-agent@9.9.9",
        node_name="broken_checkpoint",
        task_id="T-001",
    )

    sup = RunSupervisor()
    app.state.supervisor = sup
    sup.register_dispatch(run.id, dispatch.dispatch_id)

    body = {
        "name": "review-completed",
        "taskId": "T-001",
        "payload": {"plan": "X"},
    }
    resp = await client.post(
        f"/api/v1/runs/{run.id}/signals",
        json=body,
        headers=auth_headers,
    )
    # Signal endpoint itself returns 202 even on builder failure — the
    # operator's signal IS persisted; what failed is the dispatch.
    assert resp.status_code == 202, resp.text

    await db_session.refresh(dispatch)
    assert dispatch.state == DispatchState.FAILED, (
        f"expected FAILED, got state={dispatch.state!r} detail={dispatch.detail!r}"
    )
    assert dispatch.detail is not None
    assert "memory_patch_builder_failed" in dispatch.detail
    assert "ValueError" in dispatch.detail
    assert "operator delivered too early" in dispatch.detail


async def test_no_builder_preserves_legacy_envelope_shape(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    app: FastAPI,
) -> None:
    """A binding without ``memory_patch_builder`` produces the v0.1.0
    envelope shape — no ``__memory_patch`` key in the result.  Regression
    bar for every pre-FEAT-015 HumanExecutor binding.
    """
    from app.modules.ai.executors.human import HumanExecutor
    from app.modules.ai.executors.registry import ExecutorRegistry
    from app.modules.ai.supervisor import RunSupervisor

    registry = ExecutorRegistry()
    registry.register(
        "test-agent@9.9.9",
        "legacy_checkpoint",
        HumanExecutor(
            ref="human:legacy_checkpoint",
            expected_signal_name="implementation-complete",
        ),
    )
    app.state.executor_registry = registry

    run, dispatch = await _seed_run_with_builder_dispatch(
        db_session,
        agent_ref="test-agent@9.9.9",
        node_name="legacy_checkpoint",
        task_id="T-001",
    )

    sup = RunSupervisor()
    app.state.supervisor = sup
    sup.register_dispatch(run.id, dispatch.dispatch_id)

    body = {
        "name": "implementation-complete",
        "taskId": "T-001",
        "payload": {"opaque": "data"},
    }
    resp = await client.post(
        f"/api/v1/runs/{run.id}/signals",
        json=body,
        headers=auth_headers,
    )
    assert resp.status_code == 202, resp.text

    await db_session.refresh(dispatch)
    assert dispatch.state == DispatchState.COMPLETED
    assert dispatch.result is not None
    assert "__memory_patch" not in dispatch.result, (
        f"no builder → no __memory_patch; got {dispatch.result!r}"
    )
    # Legacy shape carries signal_name + task_id + payload.
    assert dispatch.result["signal_name"] == "implementation-complete"
    assert dispatch.result["task_id"] == "T-001"
    assert dispatch.result["payload"] == {"opaque": "data"}
