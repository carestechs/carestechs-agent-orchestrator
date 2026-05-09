"""BUG-013: ``mark_task_done`` advances ``current_task_id`` through a multi-task list.

The handler is a closure inside ``register_lifecycle_v03``; tests resolve
the binding via the registry and dispatch a synthetic ``DispatchContext``
with real DB-backed ``RunMemory`` rows.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.bootstrap import register_lifecycle_v03
from app.modules.ai.executors.local import LocalExecutor
from app.modules.ai.executors.registry import ExecutorRegistry
from app.modules.ai.models import Run, RunMemory
from app.modules.ai.tools.lifecycle.memory import (
    LIFECYCLE_MEMORY_NS,
    LifecycleMemory,
    LifecycleTask,
    to_run_memory,
)

_AGENT_REF = "lifecycle-agent@0.3.0"
_AGENTS_DIR = Path(__file__).resolve().parents[4] / "agents"


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def stub_lifecycle_client() -> Any:
    return MagicMock(name="FlowEngineLifecycleClient")


@pytest.fixture
def stub_llm_provider() -> Any:
    provider = MagicMock(name="LLMProvider")
    provider.name = "stub"
    provider.model = "stub-1"
    return provider


@pytest_asyncio.fixture(loop_scope="function")
async def seeded_run_ids(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[list[uuid.UUID]]:
    """Track Run ids seeded by a test and tear them down afterwards.

    The fixture-scoped engine is shared across tests; without explicit
    cleanup these Runs leak into other tests' SELECT counts.
    """
    ids: list[uuid.UUID] = []
    yield ids
    for run_id in ids:
        await _cleanup_run(session_factory, run_id)


@pytest.fixture
def registry(
    stub_lifecycle_client: Any,
    stub_llm_provider: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> ExecutorRegistry:
    reg = ExecutorRegistry()
    register_lifecycle_v03(
        reg,
        _AGENT_REF,
        lifecycle_client=stub_lifecycle_client,
        llm_provider=stub_llm_provider,
        session_factory=session_factory,
        workflow_ids={
            "work_item_workflow": uuid.uuid4(),
            "task_workflow": uuid.uuid4(),
        },
    )
    return reg


def _build_memory(*, current: str | None, completed: list[str], task_ids: list[str]) -> dict[str, Any]:
    model = LifecycleMemory(
        tasks=[LifecycleTask(id=tid, title=f"Task {tid}") for tid in task_ids],
        current_task_id=current,
        completed_task_ids=completed,
    )
    return {LIFECYCLE_MEMORY_NS: to_run_memory(model)}


async def _seed_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    memory_data: dict[str, Any],
) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Run(
                id=run_id,
                agent_ref=_AGENT_REF,
                agent_definition_hash="hash",
                intake={},
                status="running",
                started_at=datetime.now(UTC),
                trace_uri=f"/test/{run_id}",
            )
        )
        session.add(RunMemory(run_id=run_id, data=memory_data))
    return run_id


async def _cleanup_run(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
) -> None:
    from sqlalchemy import delete as _delete

    async with session_factory() as session, session.begin():
        await session.execute(_delete(RunMemory).where(RunMemory.run_id == run_id))
        await session.execute(_delete(Run).where(Run.id == run_id))


async def _read_memory(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
) -> dict[str, Any]:
    async with session_factory() as session:
        row = await session.get(RunMemory, run_id)
    assert row is not None
    return dict(row.data or {})


def _ctx(run_id: uuid.UUID) -> DispatchContext:
    return DispatchContext(
        dispatch_id=uuid.uuid4(),
        run_id=run_id,
        step_id=uuid.uuid4(),
        agent_ref=_AGENT_REF,
        node_name="mark_task_done",
        intake={},
    )


class TestMarkTaskDoneBinding:
    def test_binding_is_local_executor(self, registry: ExecutorRegistry) -> None:
        binding = registry.resolve(_AGENT_REF, "mark_task_done")
        assert isinstance(binding.executor, LocalExecutor)
        assert binding.executor.name == "local:mark_task_done"


@pytest.mark.asyncio
class TestMarkTaskDoneHandler:
    async def test_advances_current_task_and_appends_completed(
        self,
        registry: ExecutorRegistry,
        session_factory: async_sessionmaker[AsyncSession],
        seeded_run_ids: list[uuid.UUID],
    ) -> None:
        """First task in a 3-task list: appends T-1, advances to T-2."""
        memory = _build_memory(current="T-1", completed=[], task_ids=["T-1", "T-2", "T-3"])
        run_id = await _seed_run(session_factory, memory_data=memory)
        seeded_run_ids.append(run_id)

        binding = registry.resolve(_AGENT_REF, "mark_task_done")
        env = await binding.executor.dispatch(_ctx(run_id))

        assert env.state.value == "completed"
        assert env.outcome is not None
        assert env.outcome.value == "ok"
        assert env.result is not None
        assert env.result["completedTaskId"] == "T-1"
        assert env.result["nextTaskId"] == "T-2"

        patch = env.result["__memory_patch"]
        assert LIFECYCLE_MEMORY_NS in patch
        ns = patch[LIFECYCLE_MEMORY_NS]
        assert ns["completedTaskIds"] == ["T-1"]
        assert ns["currentTaskId"] == "T-2"

    async def test_last_task_clears_current_task_id(
        self,
        registry: ExecutorRegistry,
        session_factory: async_sessionmaker[AsyncSession],
        seeded_run_ids: list[uuid.UUID],
    ) -> None:
        """When the just-approved task is the only one left, current advances to None."""
        memory = _build_memory(
            current="T-3",
            completed=["T-1", "T-2"],
            task_ids=["T-1", "T-2", "T-3"],
        )
        run_id = await _seed_run(session_factory, memory_data=memory)
        seeded_run_ids.append(run_id)

        binding = registry.resolve(_AGENT_REF, "mark_task_done")
        env = await binding.executor.dispatch(_ctx(run_id))

        assert env.state.value == "completed"
        assert env.result is not None
        assert env.result["completedTaskId"] == "T-3"
        assert env.result["nextTaskId"] is None

        patch = env.result["__memory_patch"]
        ns = patch[LIFECYCLE_MEMORY_NS]
        assert ns["completedTaskIds"] == ["T-1", "T-2", "T-3"]
        assert ns["currentTaskId"] is None

    async def test_idempotent_on_repeat(
        self,
        registry: ExecutorRegistry,
        session_factory: async_sessionmaker[AsyncSession],
        seeded_run_ids: list[uuid.UUID],
    ) -> None:
        """Re-running with the current id already in completed_task_ids does not double-append.

        Defensive: shouldn't happen in the deterministic flow, but the
        handler is the only writer of ``completed_task_ids`` and should
        not corrupt it on a replayed dispatch.
        """
        memory = _build_memory(
            current="T-1",
            completed=["T-1"],
            task_ids=["T-1", "T-2"],
        )
        run_id = await _seed_run(session_factory, memory_data=memory)
        seeded_run_ids.append(run_id)

        binding = registry.resolve(_AGENT_REF, "mark_task_done")
        env = await binding.executor.dispatch(_ctx(run_id))

        assert env.state.value == "completed"
        assert env.result is not None
        patch = env.result["__memory_patch"]
        ns = patch[LIFECYCLE_MEMORY_NS]
        assert ns["completedTaskIds"] == ["T-1"]  # unchanged — no duplicate
        assert ns["currentTaskId"] == "T-2"
