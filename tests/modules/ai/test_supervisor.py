"""Tests for :class:`RunSupervisor` (T-037)."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from app.modules.ai.supervisor import RunSupervisor

# ---------------------------------------------------------------------------
# spawn + wake
# ---------------------------------------------------------------------------


class TestSpawnAndWake:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_spawn_runs_coroutine(self) -> None:
        sup = RunSupervisor()
        run_id = uuid.uuid4()
        done = asyncio.Event()

        async def coro(event: asyncio.Event) -> None:
            done.set()
            await event.wait()

        task = sup.spawn(run_id, coro)
        await asyncio.wait_for(done.wait(), timeout=0.5)
        assert sup.is_registered(run_id)

        await sup.wake(run_id)
        await asyncio.wait_for(task, timeout=0.5)
        assert not sup.is_registered(run_id)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_wake_is_observed_quickly(self) -> None:
        sup = RunSupervisor()
        run_id = uuid.uuid4()
        observed_at: list[float] = []

        async def coro(event: asyncio.Event) -> None:
            await event.wait()
            observed_at.append(time.perf_counter())

        sup.spawn(run_id, coro)
        await asyncio.sleep(0)  # let spawn settle

        t_wake = time.perf_counter()
        await sup.wake(run_id)

        # Wait for the task to complete
        while sup.is_registered(run_id):
            await asyncio.sleep(0.001)

        assert observed_at
        assert (observed_at[0] - t_wake) < 0.1  # within 100 ms (generous for CI)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_wake_before_await_is_preserved(self) -> None:
        """wake() called BEFORE the coro reaches await_wake must not be lost."""
        sup = RunSupervisor()
        run_id = uuid.uuid4()
        ready = asyncio.Event()

        async def coro(event: asyncio.Event) -> None:
            # Delay reaching the wait by 50 ms.
            await asyncio.sleep(0.05)
            ready.set()
            await event.wait()

        sup.spawn(run_id, coro)
        # Wake immediately, before the coro reaches wait().
        await sup.wake(run_id)

        await asyncio.wait_for(ready.wait(), timeout=0.5)
        # The coro should still observe the wake and exit.
        while sup.is_registered(run_id):
            await asyncio.sleep(0.001)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_wake_unknown_run_is_noop(self) -> None:
        sup = RunSupervisor()
        # Must not raise.
        await sup.wake(uuid.uuid4())


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


class TestCancel:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_cancel_exits_coro_quickly(self) -> None:
        sup = RunSupervisor()
        run_id = uuid.uuid4()

        async def coro(event: asyncio.Event) -> None:
            await event.wait()
            # If we got here without being cancelled, sleep forever.
            await asyncio.sleep(60)

        sup.spawn(run_id, coro)
        await asyncio.sleep(0)

        t = time.perf_counter()
        await sup.cancel(run_id)
        elapsed = time.perf_counter() - t

        assert not sup.is_registered(run_id)
        assert elapsed < 0.5

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cancel_sets_flag_before_cancelling(self) -> None:
        sup = RunSupervisor()
        run_id = uuid.uuid4()
        seen_flag: list[bool] = []

        async def coro(event: asyncio.Event) -> None:
            try:
                await event.wait()
                seen_flag.append(sup.is_cancelled(run_id))
            except asyncio.CancelledError:
                seen_flag.append(sup.is_cancelled(run_id))
                raise

        sup.spawn(run_id, coro)
        await asyncio.sleep(0)
        await sup.cancel(run_id)

        assert seen_flag == [True]


# ---------------------------------------------------------------------------
# exceptions in supervised coros
# ---------------------------------------------------------------------------


class TestExceptionHandling:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_exception_in_coro_does_not_crash_supervisor(self) -> None:
        sup = RunSupervisor()
        run_a = uuid.uuid4()
        run_b = uuid.uuid4()

        async def boom(event: asyncio.Event) -> None:
            raise RuntimeError("kaboom")

        async def ok(event: asyncio.Event) -> None:
            await event.wait()

        sup.spawn(run_a, boom)
        # Let the task raise and deregister.
        while sup.is_registered(run_a):
            await asyncio.sleep(0.001)

        # Supervisor is still responsive.
        sup.spawn(run_b, ok)
        assert sup.is_registered(run_b)
        await sup.wake(run_b)
        while sup.is_registered(run_b):
            await asyncio.sleep(0.001)


# ---------------------------------------------------------------------------
# shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_shutdown_cancels_remaining_tasks(self) -> None:
        sup = RunSupervisor()
        ids = [uuid.uuid4() for _ in range(3)]

        async def never_done(event: asyncio.Event) -> None:
            await asyncio.sleep(60)

        for rid in ids:
            sup.spawn(rid, never_done)
        await asyncio.sleep(0)

        t = time.perf_counter()
        await sup.shutdown(grace=0.1)
        elapsed = time.perf_counter() - t

        for rid in ids:
            assert not sup.is_registered(rid)
        assert elapsed < 1.0  # generous bound

    @pytest.mark.asyncio(loop_scope="function")
    async def test_shutdown_empty_is_noop(self) -> None:
        sup = RunSupervisor()
        await sup.shutdown(grace=0.1)


# ---------------------------------------------------------------------------
# concurrent spawns
# ---------------------------------------------------------------------------


class TestConcurrentSpawns:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_many_spawns_all_run(self) -> None:
        sup = RunSupervisor()
        counter = 0

        async def bump(event: asyncio.Event) -> None:
            nonlocal counter
            counter += 1
            await event.wait()

        ids = [uuid.uuid4() for _ in range(100)]
        for rid in ids:
            sup.spawn(rid, bump)
        await asyncio.sleep(0.01)

        assert counter == 100

        for rid in ids:
            await sup.wake(rid)
        # Drain.
        await asyncio.sleep(0.01)
        assert all(not sup.is_registered(rid) for rid in ids)


# ---------------------------------------------------------------------------
# await_wake specifics (T-053)
# ---------------------------------------------------------------------------


class TestAwaitWake:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_cancel_interrupts_await_wake(self) -> None:
        """A coro blocked on ``await_wake`` must exit within the grace
        window when ``cancel`` fires."""
        sup = RunSupervisor()
        run_id = uuid.uuid4()
        started = asyncio.Event()

        async def coro(_event: asyncio.Event) -> None:
            started.set()
            await sup.await_wake(run_id)

        sup.spawn(run_id, coro)
        await asyncio.wait_for(started.wait(), timeout=0.5)

        t = time.perf_counter()
        await sup.cancel(run_id)
        elapsed = time.perf_counter() - t
        assert elapsed < 0.5
        assert not sup.is_registered(run_id)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_await_wake_for_unknown_run_returns_immediately(self) -> None:
        sup = RunSupervisor()
        t = time.perf_counter()
        await sup.await_wake(uuid.uuid4())
        assert time.perf_counter() - t < 0.1


# ---------------------------------------------------------------------------
# Operator signals (FEAT-005 / T-096)
# ---------------------------------------------------------------------------


class TestSignalChannels:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_wait_then_deliver(self) -> None:
        sup = RunSupervisor()
        run_id = uuid.uuid4()
        seen: list[dict[str, int]] = []

        async def waiter() -> None:
            payload = await sup.await_signal(run_id, "implementation-complete", "T-001")
            seen.append(payload)

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0.01)  # let waiter reach await_signal
        sup.deliver_signal(run_id, "implementation-complete", "T-001", {"x": 1})
        await asyncio.wait_for(task, timeout=0.5)

        assert seen == [{"x": 1}]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_deliver_then_wait_preload(self) -> None:
        sup = RunSupervisor()
        run_id = uuid.uuid4()
        sup.deliver_signal(run_id, "implementation-complete", "T-001", {"y": 2})
        payload = await sup.await_signal(run_id, "implementation-complete", "T-001")
        assert payload == {"y": 2}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_independent_task_wakes(self) -> None:
        sup = RunSupervisor()
        run_id = uuid.uuid4()
        seen: list[tuple[str, dict[str, int]]] = []

        async def waiter(task_id: str) -> None:
            payload = await sup.await_signal(run_id, "implementation-complete", task_id)
            seen.append((task_id, payload))

        t1 = asyncio.create_task(waiter("T-001"))
        t2 = asyncio.create_task(waiter("T-002"))
        await asyncio.sleep(0.01)

        sup.deliver_signal(run_id, "implementation-complete", "T-002", {"task": 2})
        await asyncio.wait_for(t2, timeout=0.5)
        # Only T-002 fired; T-001 still pending.
        assert seen == [("T-002", {"task": 2})]
        assert not t1.done()

        sup.deliver_signal(run_id, "implementation-complete", "T-001", {"task": 1})
        await asyncio.wait_for(t1, timeout=0.5)
        assert ("T-001", {"task": 1}) in seen

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cancel_during_wait(self) -> None:
        sup = RunSupervisor()
        run_id = uuid.uuid4()

        async def waiter() -> None:
            await sup.await_signal(run_id, "implementation-complete", "T-001")

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio(loop_scope="function")
    async def test_redeliver_overwrites_buffer(self) -> None:
        """Double-deliver pre-wait should overwrite; the second payload wins on await."""
        sup = RunSupervisor()
        run_id = uuid.uuid4()
        sup.deliver_signal(run_id, "implementation-complete", "T-001", {"v": 1})
        sup.deliver_signal(run_id, "implementation-complete", "T-001", {"v": 2})
        payload = await sup.await_signal(run_id, "implementation-complete", "T-001")
        assert payload == {"v": 2}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_purge_on_run_termination(self) -> None:
        sup = RunSupervisor()
        run_id = uuid.uuid4()

        async def coro(_event: asyncio.Event) -> None:
            # Run buffers a signal, then exits — purge should fire.
            return

        sup.deliver_signal(run_id, "implementation-complete", "T-001", {"x": 1})
        task = sup.spawn(run_id, coro)
        await asyncio.wait_for(task, timeout=0.5)

        # After purge the buffer is empty; await_signal blocks until a fresh deliver.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                sup.await_signal(run_id, "implementation-complete", "T-001"),
                timeout=0.1,
            )
