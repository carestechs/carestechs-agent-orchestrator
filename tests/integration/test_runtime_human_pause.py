"""Regression for IMP-002 / T-269: human-mode dispatch flips Run.status to PAUSED.

The deterministic runtime parks on a ``mode=human`` dispatch awaiting an
operator signal.  Before this fix the run stayed at ``status='running'``
the whole time, conflating multi-day human handoffs with millisecond
engine waits.  The runtime now flips ``Run.status → paused`` before
awaiting and back to ``running`` on resume (success / failure / timeout
/ cancellation).

Engine and remote modes do **not** flip — those waits are not
human-handoff waits.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import NullPool, delete, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.modules.ai.agents import AgentDefinition, _parse_file
from app.modules.ai.enums import DispatchState, RunStatus, StopReason
from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.binding import _reset_exemptions_for_tests
from app.modules.ai.executors.human import HumanExecutor
from app.modules.ai.executors.local import LocalExecutor
from app.modules.ai.executors.registry import ExecutorRegistry
from app.modules.ai.models import Dispatch, Run, RunMemory, Step
from app.modules.ai.runtime_deterministic import run_deterministic_loop
from app.modules.ai.schemas import DispatchEnvelope
from app.modules.ai.supervisor import RunSupervisor
from app.modules.ai.trace import NoopTraceStore

pytestmark = pytest.mark.asyncio(loop_scope="function")


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _build_session_factory(url: str) -> async_sessionmaker[AsyncSession]:
    eng = create_async_engine(url, poolclass=NullPool)
    return async_sessionmaker(bind=eng, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _reset_exemptions() -> None:
    _reset_exemptions_for_tests()


@pytest_asyncio.fixture(loop_scope="function")
async def session_factory(
    test_database_url: str, migrated: None, fresh_pool: None
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    yield _build_session_factory(test_database_url)


def _build_human_pause_agent(tmp_path: Path) -> AgentDefinition:
    """Three-node flow: ``start`` → ``human_node`` → ``done``.

    ``start`` is the synthetic entry marker (matches the lifecycle-agent
    YAML's ``start: [load_work_item]`` shape).  ``human_node`` is bound
    to a :class:`HumanExecutor` in each test.
    """
    spec: dict[str, Any] = {
        "ref": "human-pause-test@0.1.0",
        "version": "0.1.0",
        "description": "Human-pause test agent.",
        "nodes": [
            {"name": "start", "description": "synthetic", "inputSchema": {"type": "object"}},
            {"name": "human_node", "description": "Human pause.", "inputSchema": {"type": "object"}},
            {"name": "done", "description": "Terminal sink.", "inputSchema": {"type": "object"}},
        ],
        "flow": {
            "entryNode": "start",
            "transitions": {
                "start": ["human_node"],
                "human_node": ["done"],
                "done": [],
            },
            "policy": "deterministic",
        },
        "intakeSchema": {"type": "object"},
        "terminalNodes": ["done"],
    }
    path = tmp_path / "human-pause-test@0.1.0.yaml"
    path.write_text(yaml.safe_dump(spec))
    return _parse_file(path, repo_root=tmp_path)


async def _seed_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    agent: AgentDefinition,
) -> uuid.UUID:
    async with session_factory() as session:
        run = Run(
            agent_ref=agent.ref,
            agent_definition_hash=agent.agent_definition_hash or "sha256:" + "0" * 64,
            intake={},
            status=RunStatus.PENDING,
            started_at=datetime.now(UTC),
            trace_uri="file:///tmp/human-pause.jsonl",
        )
        session.add(run)
        await session.flush()
        await session.commit()
        await session.refresh(run)
        return run.id


async def _read_status(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
) -> RunStatus:
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        return RunStatus(run.status)


async def _wait_for_status(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    target: RunStatus,
    *,
    timeout: float = 5.0,
) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if await _read_status(session_factory, run_id) == target:
            return
        await asyncio.sleep(0.02)
    actual = await _read_status(session_factory, run_id)
    raise AssertionError(
        f"Run.status never reached {target!r} within {timeout}s; last seen {actual!r}"
    )


async def _find_in_flight_dispatch(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
) -> uuid.UUID:
    async with session_factory() as session:
        result = await session.scalar(
            select(Dispatch).where(
                Dispatch.run_id == run_id,
                Dispatch.state == DispatchState.DISPATCHED.value,
            )
        )
    assert result is not None, "expected an in-flight dispatch row"
    return result.dispatch_id


async def _cleanup(
    session_factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> None:
    async with session_factory() as session:
        await session.execute(delete(Dispatch).where(Dispatch.run_id == run_id))
        await session.execute(delete(Step).where(Step.run_id == run_id))
        await session.execute(delete(RunMemory).where(RunMemory.run_id == run_id))
        await session.execute(delete(Run).where(Run.id == run_id))
        await session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def _noop(ctx: DispatchContext) -> Mapping[str, Any]:
    del ctx
    return {"ok": True}


def _register_done_terminal(registry: ExecutorRegistry, agent: AgentDefinition) -> None:
    """Terminal nodes are still dispatched; register a no-op so the loop
    can advance past ``done`` to the resolver's terminal short-circuit."""
    registry.register(
        agent.ref,
        "done",
        LocalExecutor(ref="local:done", handler=_noop),
    )


async def test_run_flips_to_paused_on_human_dispatch(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """Status sequence: pending → running → paused → running → completed."""
    agent = _build_human_pause_agent(tmp_path)
    registry = ExecutorRegistry()
    registry.register(
        agent.ref,
        "human_node",
        HumanExecutor(
            ref="human:human_node",
            expected_signal_name="implementation-complete",
        ),
    )
    _register_done_terminal(registry, agent)

    run_id = await _seed_run(session_factory, agent=agent)
    supervisor = RunSupervisor()
    loop_task: asyncio.Task[None] | None = None

    try:
        loop_task = asyncio.create_task(
            run_deterministic_loop(
                run_id=run_id,
                agent=agent,
                trace=NoopTraceStore(),
                supervisor=supervisor,
                registry=registry,
                session_factory=session_factory,
                cancel_event=asyncio.Event(),
                dispatch_timeout_seconds=10,
            )
        )

        # Wait until the runtime parks on the human dispatch and flips PAUSED.
        await _wait_for_status(session_factory, run_id, RunStatus.PAUSED)

        # Resume by delivering the dispatch.
        dispatch_id = await _find_in_flight_dispatch(session_factory, run_id)
        envelope = DispatchEnvelope(
            dispatch_id=dispatch_id,
            step_id=uuid.uuid4(),  # ignored by the runtime on resume
            run_id=run_id,
            executor_ref="human:human_node",
            mode="human",  # type: ignore[arg-type]
            state="completed",  # type: ignore[arg-type]
            intake={},
            outcome="ok",  # type: ignore[arg-type]
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            result={},
        )
        supervisor.deliver_dispatch(dispatch_id, envelope)

        await asyncio.wait_for(loop_task, timeout=5.0)

        async with session_factory() as session:
            run_row = await session.get(Run, run_id)
            assert run_row is not None
            final_status = RunStatus(run_row.status)
            assert final_status == RunStatus.COMPLETED, (
                f"status={final_status!r}, stop_reason={run_row.stop_reason!r}, "
                f"final_state={run_row.final_state!r}"
            )
    finally:
        if loop_task is not None and not loop_task.done():
            loop_task.cancel()
        await _cleanup(session_factory, run_id)


async def test_local_mode_does_not_flip_to_paused(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """A local-mode dispatch (synchronous result) never flips to PAUSED.

    Engine-mode would be the more direct mirror of the human path, but
    spinning up a stub engine executor with all its outbox plumbing is
    needless surface for this assertion.  Local-mode is sufficient to
    prove the flip is gated on ``DispatchMode.HUMAN`` specifically.
    """
    observed: list[RunStatus] = []

    async def _capture(ctx: DispatchContext) -> Mapping[str, Any]:
        observed.append(await _read_status(session_factory, ctx.run_id))
        return {"ok": True}

    agent = _build_human_pause_agent(tmp_path)
    registry = ExecutorRegistry()
    registry.register(
        agent.ref,
        "human_node",
        LocalExecutor(ref="local:human_node", handler=_capture),
    )
    _register_done_terminal(registry, agent)

    run_id = await _seed_run(session_factory, agent=agent)

    try:
        await run_deterministic_loop(
            run_id=run_id,
            agent=agent,
            trace=NoopTraceStore(),
            supervisor=RunSupervisor(),
            registry=registry,
            session_factory=session_factory,
            cancel_event=asyncio.Event(),
            dispatch_timeout_seconds=5,
        )

        # During the local dispatch the run was RUNNING — never PAUSED.
        assert observed == [RunStatus.RUNNING]
        assert await _read_status(session_factory, run_id) == RunStatus.COMPLETED
    finally:
        await _cleanup(session_factory, run_id)


async def test_cancel_while_paused_terminates_run(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """Cancellation during a human pause lands the run at CANCELLED, not running."""
    agent = _build_human_pause_agent(tmp_path)
    registry = ExecutorRegistry()
    registry.register(
        agent.ref,
        "human_node",
        HumanExecutor(
            ref="human:human_node",
            expected_signal_name="implementation-complete",
        ),
    )
    _register_done_terminal(registry, agent)

    run_id = await _seed_run(session_factory, agent=agent)
    supervisor = RunSupervisor()
    cancel_event = asyncio.Event()
    loop_task: asyncio.Task[None] | None = None

    try:
        loop_task = asyncio.create_task(
            run_deterministic_loop(
                run_id=run_id,
                agent=agent,
                trace=NoopTraceStore(),
                supervisor=supervisor,
                registry=registry,
                session_factory=session_factory,
                cancel_event=cancel_event,
                dispatch_timeout_seconds=10,
            )
        )

        await _wait_for_status(session_factory, run_id, RunStatus.PAUSED)

        # Trip the cancel signal AND deliver a synthesised cancelled
        # envelope so ``await_dispatch`` returns and the loop's
        # cancel-check fires on its next iteration.
        cancel_event.set()
        dispatch_id = await _find_in_flight_dispatch(session_factory, run_id)
        envelope = DispatchEnvelope(
            dispatch_id=dispatch_id,
            step_id=uuid.uuid4(),
            run_id=run_id,
            executor_ref="human:human_node",
            mode="human",  # type: ignore[arg-type]
            state="cancelled",  # type: ignore[arg-type]
            intake={},
            outcome="cancelled",  # type: ignore[arg-type]
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            result={},
        )
        supervisor.deliver_dispatch(dispatch_id, envelope)

        await asyncio.wait_for(loop_task, timeout=5.0)

        # Final status: CANCELLED — the post-await ``_mark_running`` flip
        # must respect terminal state set by the cancel branch and not
        # stomp it back to RUNNING.
        assert await _read_status(session_factory, run_id) == RunStatus.CANCELLED
    finally:
        if loop_task is not None and not loop_task.done():
            loop_task.cancel()
        await _cleanup(session_factory, run_id)


async def test_timeout_in_paused_state_resumes_to_failed(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """When the dispatch times out, status flips paused → running, then FAILED.

    Verifies the ``finally`` branch of the IMP-002 wrapper: even on the
    error path the resume flip fires, and the run does not get stuck at
    ``paused``.
    """
    agent = _build_human_pause_agent(tmp_path)
    registry = ExecutorRegistry()
    registry.register(
        agent.ref,
        "human_node",
        HumanExecutor(
            ref="human:human_node",
            expected_signal_name="implementation-complete",
        ),
    )
    _register_done_terminal(registry, agent)

    run_id = await _seed_run(session_factory, agent=agent)

    try:
        # ``dispatch_timeout_seconds`` is an int per the runtime
        # signature; use 1 s and never deliver the signal.
        await run_deterministic_loop(
            run_id=run_id,
            agent=agent,
            trace=NoopTraceStore(),
            supervisor=RunSupervisor(),
            registry=registry,
            session_factory=session_factory,
            cancel_event=asyncio.Event(),
            dispatch_timeout_seconds=1,
        )

        final_status = await _read_status(session_factory, run_id)
        assert final_status == RunStatus.FAILED, (
            f"timeout in paused state should land at FAILED via _terminate's "
            f"error mapping; got {final_status!r}"
        )

        async with session_factory() as session:
            run = await session.get(Run, run_id)
            assert run is not None
            # ``_terminate`` writes stop_reason=ERROR for timeout failures.
            assert run.stop_reason == StopReason.ERROR
    finally:
        await _cleanup(session_factory, run_id)
