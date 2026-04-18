"""Integration tests for lifespan-driven zombie reconciliation (T-045).

These exercise the real lifespan hook by driving the ASGI app through
``AsyncClient`` — it fires startup and shutdown the same way uvicorn does.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.modules.ai.enums import RunStatus, StopReason
from app.modules.ai.models import Run, RunMemory


@pytest_asyncio.fixture(loop_scope="function")
async def session_factory(
    engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory


async def _seed_run(
    factory: async_sessionmaker[AsyncSession], *, status: RunStatus
) -> uuid.UUID:
    async with factory() as session:
        run = Run(
            agent_ref="a",
            agent_definition_hash="sha256:" + "0" * 64,
            intake={},
            status=status,
            started_at=datetime.now(UTC),
            trace_uri="file:///tmp/t.jsonl",
        )
        session.add(run)
        await session.flush()
        session.add(RunMemory(run_id=run.id, data={}))
        await session.commit()
        return run.id


async def _fetch(
    factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> Run | None:
    async with factory() as session:
        return await session.scalar(select(Run).where(Run.id == run_id))


async def _cleanup(
    factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> None:
    async with factory() as session:
        await session.execute(
            RunMemory.__table__.delete().where(RunMemory.run_id == run_id)
        )
        await session.execute(Run.__table__.delete().where(Run.id == run_id))
        await session.commit()


@pytest_asyncio.fixture(loop_scope="function")
async def fresh_pool() -> AsyncIterator[None]:
    """Dispose the cached engine pool so this test's connections are created
    fresh on the current event loop.  Prevents cross-test loop contamination.
    """
    from app.core.database import get_engine

    await get_engine().dispose()
    yield
    await get_engine().dispose()


@pytest.mark.usefixtures("fresh_pool")
class TestLifespanZombieReconciliation:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_running_row_transitions_to_failed_on_startup(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        from app.main import create_app

        zombie_id = await _seed_run(session_factory, status=RunStatus.RUNNING)

        try:
            application = create_app()
            async with application.router.lifespan_context(application):
                pass

            run = await _fetch(session_factory, zombie_id)
            assert run is not None
            assert run.status == RunStatus.FAILED
            assert run.stop_reason == StopReason.ERROR
            assert run.ended_at is not None
            assert run.final_state is not None
            assert run.final_state.get("zombie_reason") == "process restart"
        finally:
            await _cleanup(session_factory, zombie_id)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_non_running_rows_are_untouched(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        from app.main import create_app

        pending_id = await _seed_run(session_factory, status=RunStatus.PENDING)
        completed_id = await _seed_run(session_factory, status=RunStatus.COMPLETED)

        try:
            application = create_app()
            async with application.router.lifespan_context(application):
                pass

            pending = await _fetch(session_factory, pending_id)
            completed = await _fetch(session_factory, completed_id)
            assert pending is not None
            assert pending.status == RunStatus.PENDING
            assert completed is not None
            assert completed.status == RunStatus.COMPLETED
        finally:
            await _cleanup(session_factory, pending_id)
            await _cleanup(session_factory, completed_id)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_supervisor_bound_to_app_state(self) -> None:
        from app.main import create_app
        from app.modules.ai.supervisor import RunSupervisor

        application = create_app()
        async with application.router.lifespan_context(application):
            assert isinstance(application.state.supervisor, RunSupervisor)


# ---------------------------------------------------------------------------
# T-059: multi-zombie + idempotence + graceful-shutdown distinction
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fresh_pool")
class TestLifespanExtras:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_multiple_zombies_all_reconciled_in_one_pass(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        from app.main import create_app

        zombie_ids = [
            await _seed_run(session_factory, status=RunStatus.RUNNING)
            for _ in range(3)
        ]

        try:
            application = create_app()
            async with application.router.lifespan_context(application):
                pass

            for rid in zombie_ids:
                run = await _fetch(session_factory, rid)
                assert run is not None
                assert run.status == RunStatus.FAILED
                assert run.stop_reason == StopReason.ERROR
                assert run.final_state is not None
                assert run.final_state.get("zombie_reason") == "process restart"
        finally:
            for rid in zombie_ids:
                await _cleanup(session_factory, rid)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_second_lifespan_pass_is_noop(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Once a zombie has been reconciled to ``failed``, a second lifespan
        pass finds nothing to do (idempotent)."""
        from app.core.database import get_engine
        from app.main import create_app

        zombie_id = await _seed_run(session_factory, status=RunStatus.RUNNING)

        try:
            # First lifespan — reconciles the zombie.
            app1 = create_app()
            async with app1.router.lifespan_context(app1):
                pass

            run = await _fetch(session_factory, zombie_id)
            assert run is not None
            assert run.status == RunStatus.FAILED
            first_ended_at = run.ended_at
            assert first_ended_at is not None

            # The module-level engine pool owns asyncpg connections tied to
            # the event loop; between app instances we dispose it so the
            # next checkout creates a fresh connection on this loop.
            await get_engine().dispose()

            # Second lifespan — should not touch the now-failed row.
            app2 = create_app()
            async with app2.router.lifespan_context(app2):
                pass

            run_after = await _fetch(session_factory, zombie_id)
            assert run_after is not None
            assert run_after.status == RunStatus.FAILED
            assert run_after.ended_at == first_ended_at  # unchanged
        finally:
            await _cleanup(session_factory, zombie_id)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_zombies_is_fast_noop(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """With no ``running`` rows, lifespan startup completes without touching
        any rows — distinguishes the zombie path from a graceful shutdown."""
        from app.main import create_app

        pending_id = await _seed_run(session_factory, status=RunStatus.PENDING)
        try:
            app = create_app()
            async with app.router.lifespan_context(app):
                pass

            run = await _fetch(session_factory, pending_id)
            assert run is not None
            assert run.status == RunStatus.PENDING  # unchanged
            assert run.final_state is None
        finally:
            await _cleanup(session_factory, pending_id)
