"""Tests for :class:`PostgresTraceStore` (FEAT-013 / T-273).

Coverage:

- The four already-persisted kinds (``step``, ``policy_call``,
  ``webhook_event``, ``operator_signal``) are *read* from the existing
  tables but never *written* by the store.  The ``record_*`` methods
  are no-ops by design (Decision 2 of FEAT-013).
- The two new tables (``effector_calls`` / ``executor_calls``) are
  written by ``record_effector_call`` / ``record_executor_call`` and
  read by ``read_effector_calls``.
- ``open_run_stream`` UNIONs the four sources, orders by event time,
  and projects to typed DTOs.
- ``tail_run_stream`` honors ``since=`` and ``kinds=`` filters.
- ``tail_run_stream(follow=True)`` reads backlog, polls for new rows
  every ~200 ms, and exits cleanly when the run reaches a terminal
  status.

The tests run against a real Postgres database via the shared
``engine`` fixture in ``tests/conftest.py``.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ai.enums import (
    DispatchMode,
    RunStatus,
    StepStatus,
    WebhookEventType,
    WebhookSource,
)
from app.modules.ai.models import (
    EffectorCall,
    ExecutorCall,
    PolicyCall,
    Run,
    RunSignal,
    Step,
    WebhookEvent,
)
from app.modules.ai.schemas import (
    EffectorCallDto,
    ExecutorCallDto,
    PolicyCallDto,
    RunSignalDto,
    StepDto,
    WebhookEventDto,
)
from app.modules.ai.trace_postgres import PostgresTraceStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def store(session_factory: async_sessionmaker[AsyncSession]) -> PostgresTraceStore:
    return PostgresTraceStore(session_factory)


@pytest_asyncio.fixture(loop_scope="function")
async def seeded_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[uuid.UUID]:
    """Insert a fresh ``running`` Run; clean up rows the test created.

    Cleans up steps / policy_calls / webhook_events / run_signals /
    executor_calls / the Run itself.  Effector calls are cleaned up
    separately by tests that touch them, since they're keyed on
    ``entity_id`` not ``run_id``.
    """
    run_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Run(
                id=run_id,
                agent_ref="test-agent@0.1.0",
                agent_definition_hash="abc123",
                intake={},
                status=RunStatus.RUNNING.value,
                started_at=datetime.now(UTC),
                trace_uri=f"/test/{run_id}",
            )
        )

    yield run_id

    async with session_factory() as session, session.begin():
        await session.execute(delete(ExecutorCall).where(ExecutorCall.run_id == run_id))
        await session.execute(delete(RunSignal).where(RunSignal.run_id == run_id))
        await session.execute(delete(WebhookEvent).where(WebhookEvent.run_id == run_id))
        await session.execute(delete(PolicyCall).where(PolicyCall.run_id == run_id))
        await session.execute(delete(Step).where(Step.run_id == run_id))
        await session.execute(delete(Run).where(Run.id == run_id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_step(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    *,
    step_number: int,
    node: str = "node-A",
) -> uuid.UUID:
    step_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Step(
                id=step_id,
                run_id=run_id,
                step_number=step_number,
                node_name=node,
                node_inputs={"n": step_number},
                status=StepStatus.COMPLETED.value,
                completed_at=datetime.now(UTC),
            )
        )
    return step_id


async def _insert_run_signal(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    *,
    name: str = "implementation-complete",
    task_id: str = "T-1",
) -> uuid.UUID:
    signal_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            RunSignal(
                id=signal_id,
                run_id=run_id,
                name=name,
                task_id=task_id,
                payload={"k": "v"},
                dedupe_key=f"{run_id}:{name}:{task_id}:{signal_id}",
            )
        )
    return signal_id


async def _insert_webhook_event(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    *,
    dedupe_key: str | None = None,
) -> uuid.UUID:
    event_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            WebhookEvent(
                id=event_id,
                run_id=run_id,
                event_type=WebhookEventType.NODE_STARTED.value,
                engine_run_id=f"engine-{event_id}",
                payload={"hello": "world"},
                signature_ok=True,
                source=WebhookSource.ENGINE.value,
                dedupe_key=dedupe_key or f"dk-{event_id}",
            )
        )
    return event_id


def _make_effector_dto(*, entity_id: uuid.UUID, transition: str = "task.T2") -> EffectorCallDto:
    return EffectorCallDto(
        effector_name="checks.post",
        entity_type="task",
        entity_id=entity_id,
        transition=transition,
        transition_key=transition,
        status="ok",
        duration_ms=42,
        emitted_at=datetime.now(UTC),
    )


def _make_executor_dto(*, run_id: uuid.UUID, node_name: str = "review") -> ExecutorCallDto:
    return ExecutorCallDto(
        dispatch_id=uuid.uuid4(),
        run_id=run_id,
        executor_ref=node_name,
        mode=DispatchMode.LOCAL,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        outcome=None,
    )


async def _drain(it: AsyncIterator[object]) -> list[object]:
    return [item async for item in it]


# ---------------------------------------------------------------------------
# Writers — the four already-persisted kinds are no-ops
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRecordIsNoopForPersistedKinds:
    """Decision 2 — these four ``record_*`` methods must not write rows."""

    async def test_record_step_does_not_create_a_row(
        self,
        store: PostgresTraceStore,
        session_factory: async_sessionmaker[AsyncSession],
        seeded_run: uuid.UUID,
    ) -> None:
        dto = StepDto(
            id=uuid.uuid4(),
            step_number=1,
            node_name="node-A",
            status=StepStatus.COMPLETED,
            node_inputs={},
        )
        await store.record_step(seeded_run, dto)

        async with session_factory() as session:
            count = await session.scalar(select(Step).where(Step.run_id == seeded_run).exists().select())
        assert count is False

    async def test_record_policy_call_does_not_create_a_row(
        self,
        store: PostgresTraceStore,
        session_factory: async_sessionmaker[AsyncSession],
        seeded_run: uuid.UUID,
    ) -> None:
        dto = PolicyCallDto(
            id=uuid.uuid4(),
            step_id=uuid.uuid4(),
            provider="stub",
            model="stub-1",
            selected_tool="terminate",
            tool_arguments={},
            available_tools=[],
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            created_at=datetime.now(UTC),
        )
        await store.record_policy_call(seeded_run, dto)
        async with session_factory() as session:
            count = await session.scalar(select(PolicyCall).where(PolicyCall.run_id == seeded_run).exists().select())
        assert count is False

    async def test_record_webhook_event_does_not_create_a_row(
        self,
        store: PostgresTraceStore,
        session_factory: async_sessionmaker[AsyncSession],
        seeded_run: uuid.UUID,
    ) -> None:
        dto = WebhookEventDto(
            id=uuid.uuid4(),
            event_type=WebhookEventType.NODE_STARTED,
            engine_run_id="engine-x",
            payload={},
            signature_ok=True,
            received_at=datetime.now(UTC),
        )
        await store.record_webhook_event(seeded_run, dto)
        async with session_factory() as session:
            count = await session.scalar(
                select(WebhookEvent).where(WebhookEvent.run_id == seeded_run).exists().select()
            )
        assert count is False

    async def test_record_operator_signal_does_not_create_a_row(
        self,
        store: PostgresTraceStore,
        session_factory: async_sessionmaker[AsyncSession],
        seeded_run: uuid.UUID,
    ) -> None:
        dto = RunSignalDto(
            id=uuid.uuid4(),
            run_id=seeded_run,
            name="implementation-complete",
            task_id="T-1",
            payload={},
            received_at=datetime.now(UTC),
            dedupe_key="x",
        )
        await store.record_operator_signal(seeded_run, dto)
        async with session_factory() as session:
            count = await session.scalar(select(RunSignal).where(RunSignal.run_id == seeded_run).exists().select())
        assert count is False


# ---------------------------------------------------------------------------
# Effector calls: record + read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEffectorCalls:
    async def test_record_then_read_returns_in_insertion_order(
        self,
        store: PostgresTraceStore,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        entity_id = uuid.uuid4()
        try:
            for n in range(3):
                dto = _make_effector_dto(entity_id=entity_id, transition=f"task.T{n}")
                await store.record_effector_call(entity_id, dto)

            results = await store.read_effector_calls(entity_id)
            assert [r.transition for r in results] == ["task.T0", "task.T1", "task.T2"]
            assert all(r.entity_id == entity_id for r in results)
        finally:
            async with session_factory() as session, session.begin():
                await session.execute(delete(EffectorCall).where(EffectorCall.entity_id == entity_id))

    async def test_read_with_no_rows_returns_empty_list(
        self,
        store: PostgresTraceStore,
    ) -> None:
        unknown = uuid.uuid4()
        assert await store.read_effector_calls(unknown) == []


# ---------------------------------------------------------------------------
# Executor calls: record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExecutorCalls:
    async def test_record_persists_a_row_with_run_link(
        self,
        store: PostgresTraceStore,
        session_factory: async_sessionmaker[AsyncSession],
        seeded_run: uuid.UUID,
    ) -> None:
        dto = _make_executor_dto(run_id=seeded_run, node_name="review")
        await store.record_executor_call(seeded_run, dto)

        async with session_factory() as session:
            result = await session.execute(select(ExecutorCall).where(ExecutorCall.run_id == seeded_run))
            rows = list(result.scalars().all())
        assert len(rows) == 1
        assert rows[0].node_name == "review"
        assert rows[0].dispatch_id == dto.dispatch_id
        assert rows[0].outcome == "pending"  # no terminal outcome set on the DTO


# ---------------------------------------------------------------------------
# open_run_stream — union over four sources, ordered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestOpenRunStream:
    async def test_unions_four_sources_in_event_time_order(
        self,
        store: PostgresTraceStore,
        session_factory: async_sessionmaker[AsyncSession],
        seeded_run: uuid.UUID,
    ) -> None:
        # Interleave inserts so the natural insertion order matches the
        # expected event-time ordering across kinds.
        await _insert_step(session_factory, seeded_run, step_number=1)
        await _insert_webhook_event(session_factory, seeded_run)
        await _insert_run_signal(session_factory, seeded_run)
        await _insert_step(session_factory, seeded_run, step_number=2)

        stream = await store.open_run_stream(seeded_run)
        items = await _drain(stream)
        kinds = [type(i).__name__ for i in items]
        assert kinds == ["StepDto", "WebhookEventDto", "RunSignalDto", "StepDto"]

    async def test_empty_run_yields_no_rows(
        self,
        store: PostgresTraceStore,
        seeded_run: uuid.UUID,
    ) -> None:
        stream = await store.open_run_stream(seeded_run)
        items = await _drain(stream)
        assert items == []

    async def test_step_row_resurfaces_after_status_update(
        self,
        store: PostgresTraceStore,
        session_factory: async_sessionmaker[AsyncSession],
        seeded_run: uuid.UUID,
    ) -> None:
        """A step row re-emits when its status changes — the watermark is
        ``COALESCE(completed_at, dispatched_at, created_at)`` so each
        runtime mutation advances the effective timestamp and the row
        reappears in the next poll with its new state.

        Pre-fix the watermark was ``created_at`` alone — the first sight
        of the row (often as ``pending``) advanced it past every later
        update, leaving clients stuck on a stale state.

        Drives the follow=true stream concurrently with row mutations
        and asserts the same id surfaces three times in order, one per
        state transition.
        """
        step_id = uuid.uuid4()
        # Pre-insert as pending so the very first poll has something to see.
        async with session_factory() as session, session.begin():
            session.add(
                Step(
                    id=step_id,
                    run_id=seeded_run,
                    step_number=20,
                    node_name="evolving_node",
                    node_inputs={},
                    status=StepStatus.PENDING.value,
                )
            )

        collected: list[StepDto] = []

        async def _consume() -> None:
            async for item in store.tail_run_stream(seeded_run, follow=True):
                if isinstance(item, StepDto) and item.id == step_id:
                    collected.append(item)
                    if len(collected) == 3:
                        return

        consumer = asyncio.create_task(_consume())

        # Give the tail one poll-cycle (200 ms) + a margin to catch the
        # initial pending state.
        await asyncio.sleep(0.4)

        # Transition 1: pending → in_progress.
        async with session_factory() as session, session.begin():
            row = await session.scalar(select(Step).where(Step.id == step_id))
            assert row is not None
            row.status = StepStatus.IN_PROGRESS.value
            row.dispatched_at = datetime.now(UTC)

        await asyncio.sleep(0.4)

        # Transition 2: in_progress → completed.
        async with session_factory() as session, session.begin():
            row = await session.scalar(select(Step).where(Step.id == step_id))
            assert row is not None
            row.status = StepStatus.COMPLETED.value
            row.completed_at = datetime.now(UTC)

        try:
            await asyncio.wait_for(consumer, timeout=3.0)
        except TimeoutError:
            consumer.cancel()
            raise AssertionError(
                f"expected 3 emissions for step {step_id}, got {[s.status for s in collected]}"
            ) from None

        assert [e.status for e in collected] == [
            StepStatus.PENDING,
            StepStatus.IN_PROGRESS,
            StepStatus.COMPLETED,
        ]


# ---------------------------------------------------------------------------
# tail_run_stream — filters + follow mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTailFilters:
    async def test_since_filter_skips_rows_before_threshold(
        self,
        store: PostgresTraceStore,
        session_factory: async_sessionmaker[AsyncSession],
        seeded_run: uuid.UUID,
    ) -> None:
        await _insert_step(session_factory, seeded_run, step_number=1)
        # Capture the wall-clock cut so we know what should be on each side.
        cut = datetime.now(UTC)
        await asyncio.sleep(0.01)
        await _insert_step(session_factory, seeded_run, step_number=2)

        stream = store.tail_run_stream(seeded_run, since=cut)
        items = await _drain(stream)
        step_numbers = [i.step_number for i in items if isinstance(i, StepDto)]
        # Only the later step survives the filter; ordering by created_at.
        assert step_numbers == [2]

    async def test_kinds_filter_selects_subset(
        self,
        store: PostgresTraceStore,
        session_factory: async_sessionmaker[AsyncSession],
        seeded_run: uuid.UUID,
    ) -> None:
        await _insert_step(session_factory, seeded_run, step_number=1)
        await _insert_run_signal(session_factory, seeded_run)

        stream = store.tail_run_stream(seeded_run, kinds=frozenset({"operator_signal"}))
        items = await _drain(stream)
        assert all(isinstance(i, RunSignalDto) for i in items)
        assert len(items) == 1


@pytest.mark.asyncio
class TestTailFollowMode:
    async def test_follow_returns_after_run_reaches_terminal_state(
        self,
        store: PostgresTraceStore,
        session_factory: async_sessionmaker[AsyncSession],
        seeded_run: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Squeeze the polling cadence so the test wraps up in <1 s.
        monkeypatch.setattr("app.modules.ai.trace_postgres._TAIL_POLL_SECONDS", 0.02)

        await _insert_step(session_factory, seeded_run, step_number=1)

        async def _flip_run_terminal() -> None:
            # Insert a second row while the tail is following, then flip
            # the run terminal so the tail exits.
            await asyncio.sleep(0.1)
            await _insert_step(session_factory, seeded_run, step_number=2)
            await asyncio.sleep(0.1)
            async with session_factory() as session, session.begin():
                run = await session.get(Run, seeded_run)
                assert run is not None
                run.status = RunStatus.COMPLETED.value
                run.ended_at = datetime.now(UTC)

        flipper = asyncio.create_task(_flip_run_terminal())
        stream = store.tail_run_stream(seeded_run, follow=True)
        collected: list[object] = []
        try:
            async for item in stream:
                collected.append(item)
        finally:
            await flipper

        # Both steps observed; tail exited on terminal status.
        steps = [c for c in collected if isinstance(c, StepDto)]
        assert [s.step_number for s in steps] == [1, 2]

    async def test_follow_picks_up_rows_committed_after_initial_drain(
        self,
        store: PostgresTraceStore,
        session_factory: async_sessionmaker[AsyncSession],
        seeded_run: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The high-water mark is advanced per row; later commits arrive."""
        monkeypatch.setattr("app.modules.ai.trace_postgres._TAIL_POLL_SECONDS", 0.02)

        async def _produce_and_terminate() -> None:
            await asyncio.sleep(0.05)
            for n in range(1, 4):
                await _insert_step(session_factory, seeded_run, step_number=n)
                await asyncio.sleep(0.03)
            async with session_factory() as session, session.begin():
                run = await session.get(Run, seeded_run)
                assert run is not None
                run.status = RunStatus.COMPLETED.value
                run.ended_at = datetime.now(UTC)

        producer = asyncio.create_task(_produce_and_terminate())
        stream = store.tail_run_stream(seeded_run, follow=True)
        collected: list[StepDto] = []
        async for item in stream:
            if isinstance(item, StepDto):
                collected.append(item)
        await producer

        assert [s.step_number for s in collected] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Session discipline — no session held across an iterator boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_run_stream_does_not_hold_a_session_across_yields(
    store: PostgresTraceStore,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_run: uuid.UUID,
) -> None:
    """A separate writer must be able to commit while the reader is mid-stream.

    If the reader held an open session across yields, an outer transaction
    would conflict with the writer's commit.  This test would deadlock /
    hang if the discipline were violated.
    """
    for n in range(1, 4):
        await _insert_step(session_factory, seeded_run, step_number=n)

    stream = await store.open_run_stream(seeded_run)
    seen: list[StepDto] = []
    async for dto in stream:
        if isinstance(dto, StepDto):
            seen.append(dto)
            # While iterating, a concurrent writer should be able to commit.
            await _insert_webhook_event(
                session_factory,
                seeded_run,
                dedupe_key=f"mid-{dto.step_number}",
            )
    assert len(seen) == 3
