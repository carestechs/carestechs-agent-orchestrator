"""EngineCreateExecutor unit tests (BUG-003).

Sibling to ``test_engine_executor.py``: stubs the engine HTTP boundary
with ``respx`` and runs against the test ``AsyncEngine`` so the local
``WorkItem`` insert + ``Run.intake`` update go through real SQLAlchemy.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
import respx
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ai.enums import RunStatus
from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.engine_create import EngineCreateExecutor
from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient
from app.modules.ai.models import Run, RunMemory, WorkItem
from app.modules.ai.tools.lifecycle.memory import (
    LifecycleMemory,
    WorkItemRef,
    write_lifecycle_memory,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")

_BASE = "http://engine.test"
_API_KEY = "test-api-key"
_TOKEN_RESP = {
    "data": {
        "accessToken": "jwt-xxx",
        "expiresAt": "2099-01-01T00:00:00Z",
        "tokenType": "Bearer",
    }
}


@pytest_asyncio.fixture(loop_scope="function")
async def lifecycle_client() -> AsyncIterator[FlowEngineLifecycleClient]:
    client = FlowEngineLifecycleClient(base_url=_BASE, api_key=_API_KEY, max_retries=2)
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture(loop_scope="function")
async def session_factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory


async def _seed_run_with_brief(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    work_item_id: str = "FEAT-099",
    title: str = "Test brief",
) -> uuid.UUID:
    async with session_factory() as session:
        run = Run(
            agent_ref="lifecycle-agent@0.3.0",
            agent_definition_hash="sha256:" + "0" * 64,
            intake={"workItemPath": "docs/work-items/FEAT-099.md"},
            status=RunStatus.PENDING,
            started_at=datetime.now(UTC),
            trace_uri="file:///tmp/bug003-unit.jsonl",
        )
        session.add(run)
        await session.flush()
        run_id = run.id
        memory = LifecycleMemory(
            work_item=WorkItemRef(id=work_item_id, type="FEAT", title=title, path="")
        )
        session.add(RunMemory(run_id=run_id, data=write_lifecycle_memory(memory)))
        await session.commit()
    return run_id


def _ctx(run_id: uuid.UUID) -> DispatchContext:
    return DispatchContext(
        dispatch_id=uuid.uuid4(),
        run_id=run_id,
        step_id=uuid.uuid4(),
        agent_ref="lifecycle-agent@0.3.0",
        node_name="register_work_item",
        intake={"runId": str(run_id), "nodeName": "register_work_item"},
    )


def _executor(
    lifecycle_client: FlowEngineLifecycleClient,
    session_factory: async_sessionmaker[AsyncSession],
    workflow_id: uuid.UUID,
) -> EngineCreateExecutor:
    return EngineCreateExecutor(
        ref="engine:work_item.W1",
        workflow_id=workflow_id,
        lifecycle_client=lifecycle_client,
        session_factory=session_factory,
    )


class TestSuccessPath:
    async def test_create_item_persists_local_row_and_writes_intake(
        self,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        run_id = await _seed_run_with_brief(session_factory)
        workflow_id = uuid.uuid4()
        engine_item_id = uuid.uuid4()

        try:
            with respx.mock(base_url=_BASE, assert_all_mocked=False, assert_all_called=False) as rx:
                rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
                create_route = rx.post(f"/api/workflows/{workflow_id}/items").mock(
                    return_value=Response(201, json={"data": {"id": str(engine_item_id)}})
                )
                executor = _executor(lifecycle_client, session_factory, workflow_id)
                env = await executor.dispatch(_ctx(run_id))

            assert env.state.value == "completed"
            assert env.mode.value == "local"
            assert env.outcome is not None
            assert env.outcome.value == "ok"
            assert env.result is not None
            assert env.result["engineItemId"] == str(engine_item_id)
            assert "workItemId" in env.result
            assert create_route.call_count == 1

            async with session_factory() as session:
                wi = await session.scalar(
                    select(WorkItem).where(WorkItem.engine_item_id == engine_item_id)
                )
                assert wi is not None
                assert wi.external_ref == "FEAT-099"
                assert wi.title == "Test brief"
                assert wi.status == "open"

                run_after = await session.get(Run, run_id)
                assert run_after is not None
                assert run_after.intake is not None
                assert run_after.intake.get("engineItemId") == str(engine_item_id)
                assert run_after.intake.get("workItemId") == "FEAT-099"
        finally:
            async with session_factory() as session:
                await session.execute(
                    WorkItem.__table__.delete().where(WorkItem.engine_item_id == engine_item_id)
                )
                await session.execute(RunMemory.__table__.delete().where(RunMemory.run_id == run_id))
                await session.execute(Run.__table__.delete().where(Run.id == run_id))
                await session.commit()


class TestIdempotency:
    async def test_existing_engine_item_id_reuses_row(
        self,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        run_id = await _seed_run_with_brief(session_factory)
        workflow_id = uuid.uuid4()
        engine_item_id = uuid.uuid4()

        try:
            async with session_factory() as session:
                preexisting = WorkItem(
                    external_ref="FEAT-099",
                    type="FEAT",
                    title="pre-existing",
                    status="in_progress",
                    opened_by="admin",
                    engine_item_id=engine_item_id,
                )
                session.add(preexisting)
                await session.commit()
                await session.refresh(preexisting)
                preexisting_id = preexisting.id

            with respx.mock(base_url=_BASE, assert_all_mocked=False, assert_all_called=False) as rx:
                rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
                rx.post(f"/api/workflows/{workflow_id}/items").mock(
                    return_value=Response(201, json={"data": {"id": str(engine_item_id)}})
                )
                executor = _executor(lifecycle_client, session_factory, workflow_id)
                env = await executor.dispatch(_ctx(run_id))

            assert env.state.value == "completed"
            assert env.result is not None
            assert env.result["workItemId"] == str(preexisting_id)

            async with session_factory() as session:
                count = (
                    await session.scalars(
                        select(WorkItem).where(WorkItem.engine_item_id == engine_item_id)
                    )
                ).all()
                assert len(count) == 1, "no duplicate row inserted"
        finally:
            async with session_factory() as session:
                await session.execute(
                    WorkItem.__table__.delete().where(WorkItem.engine_item_id == engine_item_id)
                )
                await session.execute(RunMemory.__table__.delete().where(RunMemory.run_id == run_id))
                await session.execute(Run.__table__.delete().where(Run.id == run_id))
                await session.commit()


    async def test_feat014_preseeded_row_is_backfilled(
        self,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """FEAT-014 seam: ``service.start_run`` inserts the ``WorkItem`` row
        with ``engine_item_id=NULL`` at run-start time.  The executor must
        find it by ``external_ref`` and backfill the engine id rather
        than INSERT-ing a second row (which would trip
        ``uq_work_items_external_ref``).
        """
        run_id = await _seed_run_with_brief(session_factory)
        workflow_id = uuid.uuid4()
        engine_item_id = uuid.uuid4()

        try:
            # FEAT-014 pre-seed: row exists with NULL engine_item_id.
            async with session_factory() as session:
                preexisting = WorkItem(
                    external_ref="FEAT-099",
                    type="FEAT",
                    title="seeded by FEAT-014",
                    status="open",
                    opened_by="upload",
                    engine_item_id=None,
                )
                session.add(preexisting)
                await session.commit()
                await session.refresh(preexisting)
                preexisting_id = preexisting.id

            with respx.mock(base_url=_BASE, assert_all_mocked=False, assert_all_called=False) as rx:
                rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
                rx.post(f"/api/workflows/{workflow_id}/items").mock(
                    return_value=Response(201, json={"data": {"id": str(engine_item_id)}})
                )
                executor = _executor(lifecycle_client, session_factory, workflow_id)
                env = await executor.dispatch(_ctx(run_id))

            assert env.state.value == "completed"
            assert env.result is not None
            assert env.result["engineItemId"] == str(engine_item_id)
            assert env.result["workItemId"] == str(preexisting_id)

            async with session_factory() as session:
                rows = (
                    await session.scalars(
                        select(WorkItem).where(WorkItem.external_ref == "FEAT-099")
                    )
                ).all()
                assert len(rows) == 1, "must not insert a second row"
                assert rows[0].engine_item_id == engine_item_id, "engine_item_id backfilled"
                assert rows[0].id == preexisting_id, "same row reused"
        finally:
            async with session_factory() as session:
                await session.execute(
                    WorkItem.__table__.delete().where(WorkItem.external_ref == "FEAT-099")
                )
                await session.execute(RunMemory.__table__.delete().where(RunMemory.run_id == run_id))
                await session.execute(Run.__table__.delete().where(Run.id == run_id))
                await session.commit()


class TestFailureSurfaces:
    async def test_missing_brief_in_memory_returns_failed_envelope(
        self,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with session_factory() as session:
            run = Run(
                agent_ref="lifecycle-agent@0.3.0",
                agent_definition_hash="sha256:" + "0" * 64,
                intake={"workItemPath": "x"},
                status=RunStatus.PENDING,
                started_at=datetime.now(UTC),
                trace_uri="file:///tmp/bug003-missing.jsonl",
            )
            session.add(run)
            await session.flush()
            run_id = run.id
            session.add(RunMemory(run_id=run_id, data={}))
            await session.commit()

        try:
            workflow_id = uuid.uuid4()
            with respx.mock(base_url=_BASE, assert_all_mocked=False, assert_all_called=False) as rx:
                rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
                create_route = rx.post(f"/api/workflows/{workflow_id}/items").mock(
                    return_value=Response(201, json={"data": {"id": str(uuid.uuid4())}})
                )
                executor = _executor(lifecycle_client, session_factory, workflow_id)
                env = await executor.dispatch(_ctx(run_id))

            assert env.state.value == "failed"
            assert env.detail is not None
            assert "lifecycle.v1 memory missing work_item brief" in env.detail
            assert create_route.call_count == 0, "must fail before calling the engine"
        finally:
            async with session_factory() as session:
                await session.execute(RunMemory.__table__.delete().where(RunMemory.run_id == run_id))
                await session.execute(Run.__table__.delete().where(Run.id == run_id))
                await session.commit()

    async def test_engine_4xx_returns_failed_envelope_with_detail(
        self,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        run_id = await _seed_run_with_brief(session_factory)
        workflow_id = uuid.uuid4()

        try:
            with respx.mock(base_url=_BASE, assert_all_mocked=False, assert_all_called=False) as rx:
                rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
                rx.post(f"/api/workflows/{workflow_id}/items").mock(
                    return_value=Response(409, json={"detail": "duplicate"})
                )
                executor = _executor(lifecycle_client, session_factory, workflow_id)
                env = await executor.dispatch(_ctx(run_id))

            assert env.state.value == "failed"
            assert env.detail is not None
            assert env.detail.startswith("engine_error")

            async with session_factory() as session:
                # No local row should leak when the engine call failed.
                rows = (
                    await session.scalars(
                        select(WorkItem).where(WorkItem.external_ref == "FEAT-099")
                    )
                ).all()
                assert rows == []
        finally:
            async with session_factory() as session:
                await session.execute(RunMemory.__table__.delete().where(RunMemory.run_id == run_id))
                await session.execute(Run.__table__.delete().where(Run.id == run_id))
                await session.commit()
