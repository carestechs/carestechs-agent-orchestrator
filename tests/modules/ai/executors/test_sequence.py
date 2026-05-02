"""SequenceEngineExecutor unit tests (BUG-004)."""

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
from app.modules.ai.executors.sequence import SequenceEngineExecutor
from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient
from app.modules.ai.models import PendingAuxWrite

pytestmark = pytest.mark.asyncio(loop_scope="function")

_BASE = "http://engine.test"
_API_KEY = "k"
_TOKEN_RESP = {
    "data": {
        "accessToken": "x",
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
    async with factory() as session:
        await session.execute(
            PendingAuxWrite.__table__.delete().where(
                PendingAuxWrite.payload["aux_type"].astext == "engine_dispatch"
            )
        )
        await session.commit()


def _ctx(item_id: uuid.UUID | None) -> DispatchContext:
    intake: dict[str, object] = {}
    if item_id is not None:
        intake["engineItemId"] = str(item_id)
    return DispatchContext(
        dispatch_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        step_id=uuid.uuid4(),
        agent_ref="lifecycle-agent@0.3.0",
        node_name="close_work_item",
        intake=intake,
    )


class TestHappyPath:
    async def test_two_hop_sequence_fires_in_order_with_correlation_per_hop(
        self,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        item_id = uuid.uuid4()
        observed: list[str] = []

        async def _post(request: object) -> Response:
            import json

            body = json.loads(request.content.decode())  # type: ignore[attr-defined]
            observed.append(body["toStatus"])
            return Response(200, json={"data": {"id": str(uuid.uuid4())}})

        with respx.mock(base_url=_BASE, assert_all_mocked=False, assert_all_called=False) as rx:
            rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
            rx.post(f"/api/items/{item_id}/transitions").mock(side_effect=_post)

            executor = SequenceEngineExecutor(
                ref="sequence:close",
                hops=[("work_item.W4", "ready"), ("work_item.W6", "closed")],
                lifecycle_client=lifecycle_client,
                session_factory=session_factory,
            )
            env = await executor.dispatch(_ctx(item_id))

        assert env.state.value == "dispatched"
        assert env.transition_key == "work_item.W6"
        assert observed == ["ready", "closed"]

        async with session_factory() as session:
            rows = (
                await session.scalars(
                    select(PendingAuxWrite).where(
                        PendingAuxWrite.entity_id == item_id,
                        PendingAuxWrite.payload["aux_type"].astext == "engine_dispatch",
                    )
                )
            ).all()
            assert len(rows) == 2, "one outbox row per hop"
            keys = sorted(r.signal_name for r in rows)
            assert keys == ["work_item.W4", "work_item.W6"]


class TestFailureSurfaces:
    async def test_second_hop_failure_returns_failed_envelope_with_hop_index(
        self,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        item_id = uuid.uuid4()
        call_count = {"n": 0}

        async def _post(_request: object) -> Response:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return Response(200, json={"data": {"id": "ok"}})
            return Response(422, json={"detail": "invalid transition"})

        with respx.mock(base_url=_BASE, assert_all_mocked=False, assert_all_called=False) as rx:
            rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
            rx.post(f"/api/items/{item_id}/transitions").mock(side_effect=_post)

            executor = SequenceEngineExecutor(
                ref="sequence:close",
                hops=[("work_item.W4", "ready"), ("work_item.W6", "closed")],
                lifecycle_client=lifecycle_client,
                session_factory=session_factory,
            )
            env = await executor.dispatch(_ctx(item_id))

        assert env.state.value == "failed"
        assert env.detail is not None
        assert "sequence_failed at hop 2/2" in env.detail
        assert "work_item.W6" in env.detail


class TestResolverPath:
    async def test_resolver_returning_none_yields_failed_envelope(
        self,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async def _resolver(_ctx: DispatchContext) -> uuid.UUID | None:
            return None

        executor = SequenceEngineExecutor(
            ref="sequence:close",
            hops=[("work_item.W4", "ready")],
            lifecycle_client=lifecycle_client,
            session_factory=session_factory,
            target_id_resolver=_resolver,
        )
        env = await executor.dispatch(_ctx(None))
        assert env.state.value == "failed"
        assert env.detail is not None
        assert "target_id_resolver returned None" in env.detail


class TestEmptyHopsRejected:
    async def test_zero_hops_raises_at_construction(
        self,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        with pytest.raises(ValueError, match="at least one hop"):
            SequenceEngineExecutor(
                ref="bad",
                hops=[],
                lifecycle_client=lifecycle_client,
                session_factory=session_factory,
            )


class TestWakeRaceFix:
    """Regression for the split-tx wake-race fix (mirrors EngineExecutor's).

    The dispatch row's ``intake.correlation_id`` MUST be visible to a
    fresh session before the first ``transition_item`` HTTP call returns,
    otherwise webhooks arriving inside the call window can't match.
    For sequences, the persisted correlation must be the *last* hop's
    (that's what wake_dispatch matches).
    """

    async def test_intake_correlation_committed_before_first_http(
        self,
        engine: AsyncEngine,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        from datetime import UTC
        from datetime import datetime as _dt

        from app.modules.ai.enums import DispatchMode, DispatchState
        from app.modules.ai.models import Dispatch, Run, RunStatus, Step

        item_id = uuid.uuid4()
        dispatch_id = uuid.uuid4()
        run_id = uuid.uuid4()
        step_id = uuid.uuid4()
        async with session_factory() as session:
            session.add(
                Run(
                    id=run_id,
                    agent_ref="t@0.1.0",
                    agent_definition_hash="sha256:" + "0" * 64,
                    intake={},
                    status=RunStatus.PENDING,
                    started_at=_dt.now(UTC),
                    trace_uri="file:///tmp/x.jsonl",
                )
            )
            session.add(
                Step(
                    id=step_id,
                    run_id=run_id,
                    step_number=1,
                    node_name="close",
                    node_inputs={},
                    status="pending",
                )
            )
            await session.flush()
            d = Dispatch(
                dispatch_id=dispatch_id,
                step_id=step_id,
                run_id=run_id,
                executor_ref="sequence:close",
                mode=DispatchMode.ENGINE.value,
                state=DispatchState.DISPATCHED.value,
                intake={"engineItemId": str(item_id), "runId": str(run_id), "nodeName": "close"},
                started_at=_dt.now(UTC),
                dispatched_at=_dt.now(UTC),
            )
            session.add(d)
            await session.commit()

        observed: list[dict[str, object]] = []

        async def _on_http(request: object) -> Response:
            # First HTTP call (hop W4) — at this exact moment the
            # outbox + intake commit MUST already be visible.  We
            # capture the intake; the test asserts it carries the
            # *last* hop's correlation, not hop W4's.
            async with session_factory() as session:
                row = await session.get(Dispatch, dispatch_id)
                assert row is not None
                observed.append(dict(row.intake or {}))
            return Response(200, json={"data": {"id": "ok"}})

        with respx.mock(base_url=_BASE, assert_all_mocked=False, assert_all_called=False) as rx:
            rx.post("/api/auth/token").mock(return_value=Response(200, json=_TOKEN_RESP))
            rx.post(f"/api/items/{item_id}/transitions").mock(side_effect=_on_http)

            executor = SequenceEngineExecutor(
                ref="sequence:close",
                hops=[("work_item.W4", "ready"), ("work_item.W6", "closed")],
                lifecycle_client=lifecycle_client,
                session_factory=session_factory,
            )
            ctx = DispatchContext(
                dispatch_id=dispatch_id,
                run_id=run_id,
                step_id=step_id,
                agent_ref="t@0.1.0",
                node_name="close",
                intake={"engineItemId": str(item_id)},
            )
            env = await executor.dispatch(ctx)

        try:
            assert env.state.value == "dispatched"
            assert env.correlation_id is not None
            # observed[0] is the intake at the FIRST hop's HTTP call.
            # The dispatch.intake.correlation_id must equal env.correlation_id
            # (which is the LAST hop's), not hop 1's.
            assert len(observed) >= 1
            intake_during_first_hop = observed[0]
            assert intake_during_first_hop.get("correlation_id") == str(env.correlation_id), (
                "BUG/wake-race regression: SequenceEngineExecutor must commit "
                "Dispatch.intake.correlation_id (with the LAST hop's correlation) "
                "BEFORE any transition_item HTTP call fires.  Without this, "
                "webhooks arriving inside the call window can't match wake."
            )
            assert intake_during_first_hop.get("transition_key") == "work_item.W6"
        finally:
            async with session_factory() as session:
                await session.execute(Dispatch.__table__.delete().where(Dispatch.dispatch_id == dispatch_id))
                await session.execute(Step.__table__.delete().where(Step.id == step_id))
                await session.execute(Run.__table__.delete().where(Run.id == run_id))
                await session.commit()
