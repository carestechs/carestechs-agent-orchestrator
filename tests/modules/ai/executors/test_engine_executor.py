"""EngineExecutor unit tests (FEAT-010 / T-231).

Stubs the engine HTTP boundary with ``respx``.  Uses a session factory
bound to the test ``AsyncEngine`` so the outbox row write goes through
real SQLAlchemy / Postgres — fresh UUIDs per test mean no inter-test
collisions; session-end teardown drops the test database.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import respx
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.engine import EngineExecutor
from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient
from app.modules.ai.models import PendingAuxWrite

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
    client = FlowEngineLifecycleClient(base_url=_BASE, api_key=_API_KEY, max_retries=3)
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture(loop_scope="function")
async def session_factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    # Clean up any PendingAuxWrite rows committed by the test — these
    # bypass the SAVEPOINT-rolled-back ``db_session`` fixture, so we
    # must drop them explicitly to keep cross-test isolation.
    async with factory() as session:
        await session.execute(
            PendingAuxWrite.__table__.delete().where(PendingAuxWrite.payload["aux_type"].astext == "engine_dispatch")
        )
        await session.commit()


def _ctx(*, item_id: uuid.UUID | None = None) -> DispatchContext:
    intake: dict[str, object] = {}
    if item_id is not None:
        intake["engineItemId"] = str(item_id)
    return DispatchContext(
        dispatch_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        step_id=uuid.uuid4(),
        agent_ref="test-agent@0.1.0",
        node_name="request_engine_transition",
        intake=intake,
    )


def _executor(
    lifecycle_client: FlowEngineLifecycleClient,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    transition_key: str = "work_item.W2",
    to_status: str = "review",
) -> EngineExecutor:
    return EngineExecutor(
        ref=f"engine:{transition_key}",
        transition_key=transition_key,
        to_status=to_status,
        lifecycle_client=lifecycle_client,
        session_factory=session_factory,
    )


class TestSuccessPath:
    async def test_2xx_returns_dispatched_envelope(
        self,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        item_id = uuid.uuid4()
        with respx.mock(base_url=_BASE, assert_all_mocked=False) as rx:
            rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
            transition_route = rx.post(f"/api/items/{item_id}/transitions").mock(
                return_value=Response(
                    200,
                    json={"data": {"id": "run-abc", "toStatus": "review"}},
                )
            )
            executor = _executor(lifecycle_client, session_factory)
            env = await executor.dispatch(_ctx(item_id=item_id))

        assert env.state.value == "dispatched"
        assert env.mode.value == "engine"
        assert env.transition_key == "work_item.W2"
        assert env.correlation_id is not None
        assert env.engine_run_id == "run-abc"
        assert transition_route.call_count == 1
        assert env.dispatched_at is not None
        assert env.outcome is None

    async def test_outbox_row_committed_with_correlation_id(
        self,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        item_id = uuid.uuid4()
        with respx.mock(base_url=_BASE, assert_all_mocked=False) as rx:
            rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
            rx.post(f"/api/items/{item_id}/transitions").mock(
                return_value=Response(200, json={"data": {"id": "run-abc"}})
            )
            executor = _executor(lifecycle_client, session_factory)
            env = await executor.dispatch(_ctx(item_id=item_id))

        async with session_factory() as session:
            row = await session.scalar(
                select(PendingAuxWrite).where(PendingAuxWrite.correlation_id == env.correlation_id)
            )
            assert row is not None
            assert row.entity_id == item_id
            assert row.entity_type == "work_item"
            assert row.payload["transition_key"] == "work_item.W2"
            assert row.payload["to_status"] == "review"
            assert row.payload["aux_type"] == "engine_dispatch"

    async def test_correlation_id_encoded_into_engine_comment(
        self,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        item_id = uuid.uuid4()
        with respx.mock(base_url=_BASE, assert_all_mocked=False) as rx:
            rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
            transition_route = rx.post(f"/api/items/{item_id}/transitions").mock(
                return_value=Response(200, json={"data": {"id": "run-abc"}})
            )
            executor = _executor(lifecycle_client, session_factory)
            env = await executor.dispatch(_ctx(item_id=item_id))

        body = transition_route.calls.last.request.content.decode("utf-8")
        assert f"orchestrator-corr:{env.correlation_id}" in body

    async def test_task_transition_key_parses_entity_type(
        self,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        item_id = uuid.uuid4()
        with respx.mock(base_url=_BASE, assert_all_mocked=False) as rx:
            rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
            rx.post(f"/api/items/{item_id}/transitions").mock(
                return_value=Response(200, json={"data": {"id": "run-abc"}})
            )
            executor = _executor(
                lifecycle_client,
                session_factory,
                transition_key="task.T6",
                to_status="impl_review",
            )
            env = await executor.dispatch(_ctx(item_id=item_id))

        async with session_factory() as session:
            row = await session.scalar(
                select(PendingAuxWrite).where(PendingAuxWrite.correlation_id == env.correlation_id)
            )
            assert row is not None
            assert row.entity_type == "task"


class TestFailurePaths:
    async def test_engine_4xx_returns_failed_no_outbox_row(
        self,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        item_id = uuid.uuid4()
        with respx.mock(base_url=_BASE, assert_all_mocked=False) as rx:
            rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
            rx.post(f"/api/items/{item_id}/transitions").mock(return_value=Response(400, text="bad transition"))
            executor = _executor(lifecycle_client, session_factory)
            env = await executor.dispatch(_ctx(item_id=item_id))

        assert env.state.value == "failed"
        assert env.outcome is not None
        assert env.outcome.value == "error"
        assert env.detail is not None
        assert "engine_error" in env.detail
        assert env.correlation_id is not None
        assert env.transition_key == "work_item.W2"
        assert env.engine_run_id is None

        async with session_factory() as session:
            row = await session.scalar(
                select(PendingAuxWrite).where(PendingAuxWrite.correlation_id == env.correlation_id)
            )
            assert row is None, "outbox row must roll back on engine failure"

    async def test_missing_engine_item_id_returns_failed_no_engine_call(
        self,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        # No respx routes registered: a stray engine call would surface as
        # respx's pass-through error.  The executor must short-circuit.
        with respx.mock(base_url=_BASE, assert_all_called=False, assert_all_mocked=True):
            executor = _executor(lifecycle_client, session_factory)
            env = await executor.dispatch(_ctx(item_id=None))

        assert env.state.value == "failed"
        assert env.detail is not None
        assert "engineItemId" in env.detail
        assert env.correlation_id is not None  # carried for trace joinability

    async def test_malformed_engine_item_id_returns_failed(
        self,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        ctx = DispatchContext(
            dispatch_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            step_id=uuid.uuid4(),
            agent_ref="test-agent@0.1.0",
            node_name="n",
            intake={"engineItemId": "not-a-uuid"},
        )
        executor = _executor(lifecycle_client, session_factory)
        env = await executor.dispatch(ctx)
        assert env.state.value == "failed"
        assert "malformed" in (env.detail or "")


# Import-quarantine assertion lives in ``tests/test_engine_executor_import_quarantine.py``
# (T-237).  Kept out of this file so the deferred wakeup of ``runtime_deterministic``
# is exercised in a fresh subprocess, not inside the executor unit-test module.
