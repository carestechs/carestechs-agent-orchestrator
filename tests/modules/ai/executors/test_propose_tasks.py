"""ProposeTasksExecutor unit tests (BUG-004 / BUG-007).

The ordering invariant under test (BUG-007): the local ``tasks`` row
MUST be inserted and committed *before* the T2 / T4 transitions fire,
so the engine's webhook for those transitions can find a matching local
row when the reactor looks up by ``engine_item_id``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ai.enums import RunStatus
from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.propose_tasks import ProposeTasksExecutor
from app.modules.ai.models import Run, RunMemory, Task, WorkItem
from app.modules.ai.tools.lifecycle.memory import (
    LIFECYCLE_MEMORY_NS,
    LifecycleMemory,
    LifecycleTask,
    WorkItemRef,
    write_lifecycle_memory,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")


@pytest_asyncio.fixture(loop_scope="function")
async def session_factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory


class _RecordingClient:
    """In-process fake of FlowEngineLifecycleClient for ordering assertions.

    ``call_log`` is an append-only list of ``(method, ...args)`` tuples
    so tests can assert the precise order of HTTP calls — the BUG-007
    fix mandates ``upsert_local_task`` runs between ``create_item`` and
    the first ``transition_item`` for each task.

    A test-supplied ``on_create_item`` / ``on_transition_item`` hook can
    inspect the local DB at the moment the engine call would fire — that
    is what the ordering test uses to prove the local row is committed
    before T2.
    """

    def __init__(self) -> None:
        self.call_log: list[tuple[str, dict[str, object]]] = []
        self._next_engine_id_idx = 0
        self.on_transition_item: object = None  # set by test

    async def create_item(
        self,
        *,
        workflow_id: uuid.UUID,
        title: str,
        external_ref: str,
        metadata: dict[str, object] | None = None,
    ) -> uuid.UUID:
        new_id = uuid.uuid4()
        self.call_log.append(
            (
                "create_item",
                {
                    "workflow_id": workflow_id,
                    "title": title,
                    "external_ref": external_ref,
                    "metadata": metadata or {},
                    "returned_id": new_id,
                },
            )
        )
        return new_id

    async def transition_item(
        self,
        *,
        item_id: uuid.UUID,
        to_status: str,
        correlation_id: uuid.UUID,
        actor: str | None = None,
        comment: str | None = None,
    ) -> dict[str, object]:
        self.call_log.append(
            (
                "transition_item",
                {
                    "item_id": item_id,
                    "to_status": to_status,
                    "correlation_id": correlation_id,
                },
            )
        )
        if self.on_transition_item is not None:
            await self.on_transition_item(item_id, to_status)  # type: ignore[misc]
        return {"id": str(uuid.uuid4())}


async def _seed_run_with_tasks(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tasks: list[LifecycleTask],
    work_item_external_ref: str = "FEAT-099",
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed Run + RunMemory + WorkItem with engine_item_id wired into intake."""
    engine_work_item_id = uuid.uuid4()
    async with session_factory() as session:
        wi = WorkItem(
            external_ref=work_item_external_ref,
            type="FEAT",
            title="propose-tasks ordering test",
            status="open",
            opened_by="lifecycle-agent",
            engine_item_id=engine_work_item_id,
        )
        session.add(wi)
        await session.flush()
        wi_local_id = wi.id

        run = Run(
            agent_ref="lifecycle-agent@0.3.0",
            agent_definition_hash="sha256:" + "0" * 64,
            intake={"engineItemId": str(engine_work_item_id)},
            status=RunStatus.PENDING,
            started_at=datetime.now(UTC),
            trace_uri="file:///tmp/bug007-unit.jsonl",
        )
        session.add(run)
        await session.flush()
        run_id = run.id

        memory = LifecycleMemory(
            work_item=WorkItemRef(
                id=work_item_external_ref, type="FEAT", title="x", path=""
            ),
            tasks=tasks,
            current_task_id=tasks[0].id if tasks else None,
        )
        session.add(RunMemory(run_id=run_id, data=write_lifecycle_memory(memory)))
        await session.commit()

    return run_id, wi_local_id, engine_work_item_id


def _ctx(run_id: uuid.UUID) -> DispatchContext:
    return DispatchContext(
        dispatch_id=uuid.uuid4(),
        run_id=run_id,
        step_id=uuid.uuid4(),
        agent_ref="lifecycle-agent@0.3.0",
        node_name="propose_tasks",
        intake={"runId": str(run_id), "nodeName": "propose_tasks"},
    )


class TestRowExistsBeforeTransitions:
    """BUG-007 regression: the local tasks row must be visible to a
    fresh DB session before the first transition_item fires."""

    async def test_t2_finds_local_row_already_committed(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        run_id, wi_local_id, engine_wi_id = await _seed_run_with_tasks(
            session_factory,
            tasks=[
                LifecycleTask(id="T-1", title="task one", executor="claude-code"),
            ],
        )

        observed_rows_at_first_transition: list[Task | None] = []

        async def _on_transition(item_id: uuid.UUID, to_status: str) -> None:
            # First transition for any task is T2 (approved). At that
            # moment, query a fresh session for a Task row keyed on the
            # engine id. BUG-007 demands it already be visible.
            if to_status != "approved":
                return
            async with session_factory() as session:
                task = await session.scalar(
                    select(Task).where(Task.engine_item_id == item_id)
                )
                observed_rows_at_first_transition.append(task)

        client = _RecordingClient()
        client.on_transition_item = _on_transition

        executor = ProposeTasksExecutor(
            ref="propose_tasks",
            task_workflow_id=uuid.uuid4(),
            lifecycle_client=client,  # type: ignore[arg-type]
            session_factory=session_factory,
        )
        env = await executor.dispatch(_ctx(run_id))

        try:
            assert env.state.value == "completed", env.detail
            assert len(observed_rows_at_first_transition) == 1
            row = observed_rows_at_first_transition[0]
            assert row is not None, (
                "BUG-007 regression: local tasks row was not committed "
                "before the T2 transition fired — reactor's webhook "
                "lookup would skip every event for this engine task id"
            )
            assert row.external_ref == "T-1"
            assert row.work_item_id == wi_local_id

            # The call sequence should be exactly:
            # create_item, transition_item(approved), transition_item(assigning),
            # then for the work item: transition_item(in_progress).
            method_seq = [c[0] for c in client.call_log]
            assert method_seq == [
                "create_item",
                "transition_item",
                "transition_item",
                "transition_item",
            ]
        finally:
            async with session_factory() as session:
                await session.execute(
                    Task.__table__.delete().where(Task.work_item_id == wi_local_id)
                )
                await session.execute(
                    RunMemory.__table__.delete().where(RunMemory.run_id == run_id)
                )
                await session.execute(Run.__table__.delete().where(Run.id == run_id))
                await session.execute(
                    WorkItem.__table__.delete().where(WorkItem.id == wi_local_id)
                )
                await session.commit()


class TestMemoryEngineIdPersistedPerTask:
    """BUG-007: per-task engine_item_id must land in memory inside the
    loop, so a later task's failure preserves earlier tasks' ids."""

    async def test_memory_carries_engine_id_per_task_after_dispatch(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        run_id, wi_local_id, _ = await _seed_run_with_tasks(
            session_factory,
            tasks=[
                LifecycleTask(id="T-1", title="t1"),
                LifecycleTask(id="T-2", title="t2"),
            ],
        )
        client = _RecordingClient()
        executor = ProposeTasksExecutor(
            ref="propose_tasks",
            task_workflow_id=uuid.uuid4(),
            lifecycle_client=client,  # type: ignore[arg-type]
            session_factory=session_factory,
        )
        env = await executor.dispatch(_ctx(run_id))

        try:
            assert env.state.value == "completed", env.detail

            async with session_factory() as session:
                mem = await session.scalar(
                    select(RunMemory).where(RunMemory.run_id == run_id)
                )
                assert mem is not None
                ns = (mem.data or {}).get(LIFECYCLE_MEMORY_NS) or {}
                tasks_in_mem = ns.get("tasks") or []
            ids = {t["id"]: t.get("engineItemId") for t in tasks_in_mem}
            assert set(ids.keys()) == {"T-1", "T-2"}
            assert all(v is not None for v in ids.values()), (
                f"every task in memory should carry an engineItemId; got {ids}"
            )
        finally:
            async with session_factory() as session:
                await session.execute(
                    Task.__table__.delete().where(Task.work_item_id == wi_local_id)
                )
                await session.execute(
                    RunMemory.__table__.delete().where(RunMemory.run_id == run_id)
                )
                await session.execute(Run.__table__.delete().where(Run.id == run_id))
                await session.execute(
                    WorkItem.__table__.delete().where(WorkItem.id == wi_local_id)
                )
                await session.commit()
