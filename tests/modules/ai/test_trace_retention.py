"""Tests for the FEAT-013 / T-277 retention sweep.

Coverage:

- Rows newer than the cutoff are retained.
- Rows older than the cutoff are deleted (or counted under ``--dry-run``).
- ``dry_run=True`` writes nothing.
- A second sweep on unchanged data is a no-op.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ai.models import EffectorCall, ExecutorCall, Run
from app.modules.ai.trace_retention import sweep


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


async def _insert_effector(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    age_days: int,
) -> int:
    """Insert one effector_calls row backdated by ``age_days``.  Returns its id."""
    backdated = datetime.now(UTC) - timedelta(days=age_days)
    async with session_factory() as session, session.begin():
        row = EffectorCall(
            entity_id=uuid.uuid4(),
            transition_key="task.T1",
            payload={},
            outcome="ok",
            created_at=backdated,
        )
        session.add(row)
        await session.flush()
        rid = row.id
    return rid


async def _insert_executor(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: uuid.UUID,
    age_days: int,
) -> int:
    backdated = datetime.now(UTC) - timedelta(days=age_days)
    async with session_factory() as session, session.begin():
        row = ExecutorCall(
            run_id=run_id,
            node_name="node-X",
            dispatch_id=uuid.uuid4(),
            payload={},
            outcome="ok",
            created_at=backdated,
        )
        session.add(row)
        await session.flush()
        rid = row.id
    return rid


@pytest_asyncio.fixture(loop_scope="function")
async def seeded_run_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Run(
                id=run_id,
                agent_ref="test@0.0.1",
                agent_definition_hash="hash",
                intake={},
                status="completed",
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
                trace_uri=f"/test/{run_id}",
            )
        )
    return run_id


@pytest.mark.asyncio
async def test_dry_run_reports_but_does_not_delete(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_run_id: uuid.UUID,
) -> None:
    old = await _insert_effector(session_factory, age_days=30)
    new = await _insert_effector(session_factory, age_days=1)
    old_exec = await _insert_executor(session_factory, run_id=seeded_run_id, age_days=30)
    new_exec = await _insert_executor(session_factory, run_id=seeded_run_id, age_days=1)

    try:
        async with session_factory() as session:
            report = await sweep(session, retention_days=14, dry_run=True)

        assert report.effector_calls_deleted == 1
        assert report.executor_calls_deleted == 1
        assert report.dry_run is True

        # No rows actually removed.
        async with session_factory() as session:
            assert await session.scalar(select(EffectorCall.id).where(EffectorCall.id == old)) == old
            assert await session.scalar(select(EffectorCall.id).where(EffectorCall.id == new)) == new
            assert await session.scalar(select(ExecutorCall.id).where(ExecutorCall.id == old_exec)) == old_exec
            assert await session.scalar(select(ExecutorCall.id).where(ExecutorCall.id == new_exec)) == new_exec
    finally:
        async with session_factory() as session, session.begin():
            await session.execute(delete(EffectorCall).where(EffectorCall.id.in_([old, new])))
            await session.execute(delete(ExecutorCall).where(ExecutorCall.id.in_([old_exec, new_exec])))
            await session.execute(delete(Run).where(Run.id == seeded_run_id))


@pytest.mark.asyncio
async def test_commit_deletes_only_old_rows(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_run_id: uuid.UUID,
) -> None:
    old = await _insert_effector(session_factory, age_days=30)
    new = await _insert_effector(session_factory, age_days=1)

    try:
        async with session_factory() as session:
            report = await sweep(session, retention_days=14, dry_run=False)
        assert report.effector_calls_deleted == 1
        assert report.dry_run is False

        async with session_factory() as session:
            assert await session.scalar(select(EffectorCall.id).where(EffectorCall.id == old)) is None
            assert await session.scalar(select(EffectorCall.id).where(EffectorCall.id == new)) == new
    finally:
        async with session_factory() as session, session.begin():
            await session.execute(delete(EffectorCall).where(EffectorCall.id == new))
            await session.execute(delete(Run).where(Run.id == seeded_run_id))


@pytest.mark.asyncio
async def test_second_sweep_is_a_noop(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_run_id: uuid.UUID,
) -> None:
    old = await _insert_effector(session_factory, age_days=30)
    try:
        async with session_factory() as session:
            first = await sweep(session, retention_days=14, dry_run=False)
        assert first.effector_calls_deleted == 1
        async with session_factory() as session:
            second = await sweep(session, retention_days=14, dry_run=False)
        assert second.effector_calls_deleted == 0
        assert second.executor_calls_deleted == 0
    finally:
        async with session_factory() as session, session.begin():
            await session.execute(delete(EffectorCall).where(EffectorCall.id == old))
            await session.execute(delete(Run).where(Run.id == seeded_run_id))


@pytest.mark.asyncio
async def test_zero_or_negative_retention_days_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        with pytest.raises(ValueError, match="retention_days must be > 0"):
            await sweep(session, retention_days=0, dry_run=True)
        with pytest.raises(ValueError, match="retention_days must be > 0"):
            await sweep(session, retention_days=-1, dry_run=True)
