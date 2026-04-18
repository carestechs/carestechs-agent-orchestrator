"""Control-flow tests for ``run_loop`` using fakes (T-039).

These exercise the branches of the loop that don't require a full webhook
round-trip.  The behavioural end-to-end (real webhook reconciliation,
done_node termination, trace file readable) is covered by T-054.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.core.exceptions import EngineError
from app.core.llm import StubLLMProvider
from app.modules.ai.agents import AgentDefinition
from app.modules.ai.enums import RunStatus, StopReason
from app.modules.ai.models import PolicyCall, Run, RunMemory, Step
from app.modules.ai.runtime import run_loop
from app.modules.ai.supervisor import RunSupervisor
from app.modules.ai.trace import NoopTraceStore

_FIXTURE = (
    Path(__file__).parent.parent.parent / "fixtures" / "agents" / "sample-linear.yaml"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_agent() -> AgentDefinition:
    return AgentDefinition.model_validate(yaml.safe_load(_FIXTURE.read_text()))


class _FakeEngine:
    """Test double: optionally raises on dispatch, optionally records calls."""

    def __init__(self, *, raise_on_dispatch: Exception | None = None) -> None:
        self._raise = raise_on_dispatch
        self.dispatched: list[tuple[str, dict[str, Any]]] = []

    async def dispatch_node(
        self,
        *,
        run_id: uuid.UUID,
        step_id: uuid.UUID,
        agent_ref: str,
        node_name: str,
        node_inputs: dict[str, Any],
    ) -> str:
        if self._raise is not None:
            raise self._raise
        self.dispatched.append((node_name, node_inputs))
        return f"eng-{step_id}"


@pytest_asyncio.fixture(loop_scope="function")
async def session_factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Per-test sessionmaker bound to the shared test engine.

    Unlike the ``db_session`` fixture, this returns a factory so the loop can
    open its own sessions per iteration.  Commits from those sessions are
    visible across the test (no SAVEPOINT wrapping), so tests should seed
    data via the factory too, not via ``db_session``.
    """
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory


async def _seed_run(
    factory: async_sessionmaker[AsyncSession],
    *,
    max_steps: int | None = None,
) -> uuid.UUID:
    """Insert a fresh Run + empty RunMemory and return the run id."""
    async with factory() as session:
        run = Run(
            agent_ref="sample-linear@1.0",
            agent_definition_hash="sha256:" + "0" * 64,
            intake={"brief": "hello"},
            status=RunStatus.PENDING,
            started_at=datetime.now(UTC),
            trace_uri="file:///tmp/t.jsonl",
        )
        session.add(run)
        await session.flush()
        session.add(RunMemory(run_id=run.id, data={}))
        await session.commit()
        return run.id


async def _cleanup_run(factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> None:
    """Best-effort teardown so tests don't leak rows across the session DB."""
    async with factory() as session:
        await session.execute(
            PolicyCall.__table__.delete().where(PolicyCall.run_id == run_id)
        )
        await session.execute(Step.__table__.delete().where(Step.run_id == run_id))
        await session.execute(RunMemory.__table__.delete().where(RunMemory.run_id == run_id))
        await session.execute(Run.__table__.delete().where(Run.id == run_id))
        await session.commit()


async def _fetch_run(
    factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> Run | None:
    async with factory() as session:
        return await session.scalar(select(Run).where(Run.id == run_id))


async def _fetch_steps(
    factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> list[Step]:
    async with factory() as session:
        result = await session.execute(
            select(Step).where(Step.run_id == run_id).order_by(Step.step_number)
        )
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStopConditionsWithoutDispatch:
    """Branches that never call the engine — fastest + most isolated."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_terminate_tool_ends_with_policy_terminated(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run_id = await _seed_run(session_factory)
        try:
            await run_loop(
                run_id=run_id,
                agent=_load_agent(),
                policy=StubLLMProvider([("terminate", {})]),
                engine=_FakeEngine(),  # type: ignore[arg-type]
                trace=NoopTraceStore(),
                supervisor=RunSupervisor(),
                session_factory=session_factory,
                cancel_event=asyncio.Event(),
            )
            run = await _fetch_run(session_factory, run_id)
            assert run is not None
            assert run.status == RunStatus.COMPLETED
            assert run.stop_reason == StopReason.POLICY_TERMINATED
            assert run.ended_at is not None
            # No step was dispatched
            assert await _fetch_steps(session_factory, run_id) == []
        finally:
            await _cleanup_run(session_factory, run_id)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_exhausted_policy_script_ends_with_error(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run_id = await _seed_run(session_factory)
        try:
            await run_loop(
                run_id=run_id,
                agent=_load_agent(),
                policy=StubLLMProvider([]),  # empty script → ProviderError on first call
                engine=_FakeEngine(),  # type: ignore[arg-type]
                trace=NoopTraceStore(),
                supervisor=RunSupervisor(),
                session_factory=session_factory,
                cancel_event=asyncio.Event(),
            )
            run = await _fetch_run(session_factory, run_id)
            assert run is not None
            assert run.status == RunStatus.FAILED
            assert run.stop_reason == StopReason.ERROR
            assert "policy_error" in (run.final_state or {})
        finally:
            await _cleanup_run(session_factory, run_id)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cancelled_before_first_iteration(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run_id = await _seed_run(session_factory)
        try:
            cancel_event = asyncio.Event()
            cancel_event.set()

            await run_loop(
                run_id=run_id,
                agent=_load_agent(),
                policy=StubLLMProvider([]),
                engine=_FakeEngine(),  # type: ignore[arg-type]
                trace=NoopTraceStore(),
                supervisor=RunSupervisor(),
                session_factory=session_factory,
                cancel_event=cancel_event,
            )
            run = await _fetch_run(session_factory, run_id)
            assert run is not None
            assert run.status == RunStatus.CANCELLED
            assert run.stop_reason == StopReason.CANCELLED
        finally:
            await _cleanup_run(session_factory, run_id)


class TestEngineFailure:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_engine_error_terminates_run_with_error(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run_id = await _seed_run(session_factory)
        try:
            failing_engine = _FakeEngine(
                raise_on_dispatch=EngineError(
                    "boom",
                    engine_http_status=502,
                    engine_correlation_id="corr-1",
                    original_body="b",
                )
            )

            await run_loop(
                run_id=run_id,
                agent=_load_agent(),
                policy=StubLLMProvider([("analyze_brief", {"brief": "hi"})]),
                engine=failing_engine,  # type: ignore[arg-type]
                trace=NoopTraceStore(),
                supervisor=RunSupervisor(),
                session_factory=session_factory,
                cancel_event=asyncio.Event(),
            )

            run = await _fetch_run(session_factory, run_id)
            assert run is not None
            assert run.status == RunStatus.FAILED
            assert run.stop_reason == StopReason.ERROR

            steps = await _fetch_steps(session_factory, run_id)
            assert len(steps) == 1
            assert steps[0].status == "failed"
            assert steps[0].error is not None
            assert steps[0].error["engine_http_status"] == 502
        finally:
            await _cleanup_run(session_factory, run_id)


class TestWebhookDrivenProgress:
    """Happy-path-ish: scheduler wakes the loop via supervisor.wake."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_webhook_wake_advances_loop(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """One dispatch → helper marks step completed + wakes → loop resumes and terminates."""
        run_id = await _seed_run(session_factory)
        supervisor = RunSupervisor()
        engine = _FakeEngine()

        try:
            # Helper that waits for the step to appear, marks it completed,
            # then wakes the loop.  Simulates what ingest_engine_event +
            # reconciliation would do in production.
            async def _webhook_driver() -> None:
                for _ in range(500):
                    steps = await _fetch_steps(session_factory, run_id)
                    if steps and steps[0].status == "dispatched":
                        break
                    await asyncio.sleep(0.01)
                else:
                    return
                async with session_factory() as session:
                    step = await session.scalar(
                        select(Step).where(Step.run_id == run_id)
                    )
                    assert step is not None
                    step.status = "completed"
                    step.node_result = {"plan": "draft-1"}
                    step.completed_at = datetime.now(UTC)
                    await session.commit()
                await supervisor.wake(run_id)

            driver_task = asyncio.create_task(_webhook_driver())

            # Run the loop via supervisor.spawn so await_wake works as in production.
            def _factory(event: asyncio.Event):  # type: ignore[no-untyped-def]
                return run_loop(
                    run_id=run_id,
                    agent=_load_agent(),
                    policy=StubLLMProvider(
                        [
                            ("analyze_brief", {"brief": "hi"}),
                            ("terminate", {}),
                        ]
                    ),
                    engine=engine,  # type: ignore[arg-type]
                    trace=NoopTraceStore(),
                    supervisor=supervisor,
                    session_factory=session_factory,
                    cancel_event=event,
                )

            loop_task = supervisor.spawn(run_id, _factory)
            await asyncio.wait_for(loop_task, timeout=10.0)
            await driver_task

            run = await _fetch_run(session_factory, run_id)
            assert run is not None
            assert run.status == RunStatus.COMPLETED
            assert run.stop_reason == StopReason.POLICY_TERMINATED

            steps = await _fetch_steps(session_factory, run_id)
            assert len(steps) == 1
            assert steps[0].status == "completed"

            # Memory was merged from node_result
            async with session_factory() as session:
                memory = await session.scalar(
                    select(RunMemory).where(RunMemory.run_id == run_id)
                )
                assert memory is not None
                assert memory.data == {"plan": "draft-1"}
        finally:
            await _cleanup_run(session_factory, run_id)
