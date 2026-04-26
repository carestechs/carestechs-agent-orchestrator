"""RunSupervisor dispatch primitives (FEAT-009 / T-219)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from app.modules.ai.schemas import DispatchEnvelope
from app.modules.ai.supervisor import DispatchCancelled, RunSupervisor

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _envelope(dispatch_id: uuid.UUID, run_id: uuid.UUID) -> DispatchEnvelope:
    return DispatchEnvelope(
        dispatch_id=dispatch_id,
        step_id=uuid.uuid4(),
        run_id=run_id,
        executor_ref="stub",
        mode="local",  # type: ignore[arg-type]
        state="completed",  # type: ignore[arg-type]
        intake={},
        outcome="ok",  # type: ignore[arg-type]
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )


class TestDispatchPrimitives:
    async def test_deliver_then_await_returns_buffered(self) -> None:
        sup = RunSupervisor()
        run_id, dispatch_id = uuid.uuid4(), uuid.uuid4()
        sup.register_dispatch(run_id, dispatch_id)
        env = _envelope(dispatch_id, run_id)
        sup.deliver_dispatch(dispatch_id, env)

        result = await sup.await_dispatch(dispatch_id)
        assert result.dispatch_id == dispatch_id

    async def test_await_then_deliver_resolves_waiter(self) -> None:
        sup = RunSupervisor()
        run_id, dispatch_id = uuid.uuid4(), uuid.uuid4()
        sup.register_dispatch(run_id, dispatch_id)
        env = _envelope(dispatch_id, run_id)

        async def deliver_after_yield() -> None:
            await asyncio.sleep(0)  # let waiter start
            sup.deliver_dispatch(dispatch_id, env)

        waiter_task = asyncio.create_task(sup.await_dispatch(dispatch_id))
        deliver_task = asyncio.create_task(deliver_after_yield())
        result, _ = await asyncio.gather(waiter_task, deliver_task)
        assert result.dispatch_id == dispatch_id

    async def test_double_deliver_is_noop(self) -> None:
        sup = RunSupervisor()
        run_id, dispatch_id = uuid.uuid4(), uuid.uuid4()
        sup.register_dispatch(run_id, dispatch_id)
        env = _envelope(dispatch_id, run_id)
        sup.deliver_dispatch(dispatch_id, env)
        sup.deliver_dispatch(dispatch_id, env)  # must not raise
        assert (await sup.await_dispatch(dispatch_id)).dispatch_id == dispatch_id

    async def test_lazy_register_on_await(self) -> None:
        """``await_dispatch`` without prior register still works (fallback path)."""
        sup = RunSupervisor()
        dispatch_id = uuid.uuid4()
        env = _envelope(dispatch_id, uuid.uuid4())

        async def deliver() -> None:
            await asyncio.sleep(0)
            sup.deliver_dispatch(dispatch_id, env)

        waiter = asyncio.create_task(sup.await_dispatch(dispatch_id))
        await asyncio.gather(waiter, deliver())
        assert waiter.result().dispatch_id == dispatch_id

    async def test_deliver_unknown_id_logged_and_dropped(self) -> None:
        sup = RunSupervisor()
        # No register, no await — just deliver. Must not raise.
        sup.deliver_dispatch(uuid.uuid4(), _envelope(uuid.uuid4(), uuid.uuid4()))


class TestRunCancelPropagation:
    async def test_pending_dispatches_cancelled_when_run_terminates(self) -> None:
        sup = RunSupervisor()
        run_id = uuid.uuid4()
        d1, d2 = uuid.uuid4(), uuid.uuid4()
        sup.register_dispatch(run_id, d1)
        sup.register_dispatch(run_id, d2)

        # Start awaiters then trigger purge (simulating run end).
        w1 = asyncio.create_task(sup.await_dispatch(d1))
        w2 = asyncio.create_task(sup.await_dispatch(d2))
        await asyncio.sleep(0)
        sup._purge_signals_for_run(run_id)

        with pytest.raises(DispatchCancelled):
            await w1
        with pytest.raises(DispatchCancelled):
            await w2
