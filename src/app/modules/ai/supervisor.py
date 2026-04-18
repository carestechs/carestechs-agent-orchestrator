"""In-process registry for supervised run-loop tasks.

Owns one :class:`asyncio.Task` + wake-up :class:`asyncio.Event` per run.
Webhooks call :meth:`RunSupervisor.wake`; the loop awaits
:meth:`RunSupervisor.await_wake` between dispatches.  Cancellation is the
choke-point for ``cancel_run`` (T-042).  Graceful shutdown drains all
tasks within a configurable grace window.

**Single-worker constraint.** The supervisor is process-local; running
uvicorn with ``--workers > 1`` would give each process its own supervisor
with no cross-worker coordination.  v1 ships single-worker only;
documented in CLAUDE.md (T-061).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class _SupervisedRun:
    run_id: uuid.UUID
    task: asyncio.Task[None]
    wake_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_requested: bool = False


class RunSupervisor:
    """Registry of in-flight runs keyed by ``run_id``."""

    def __init__(self) -> None:
        self._runs: dict[uuid.UUID, _SupervisedRun] = {}
        self._lock = asyncio.Lock()
        # FEAT-005 / T-096 — per-(run, name, task) signal channels.
        # Events fire the first matching ``await_signal`` call; buffered
        # payloads let ``deliver_signal`` arrive *before* the waiter.
        self._signal_events: dict[
            tuple[uuid.UUID, str, str], asyncio.Event
        ] = {}
        self._signal_buffers: dict[
            tuple[uuid.UUID, str, str], dict[str, Any]
        ] = {}

    # -- Spawn / cancel / wake --------------------------------------------

    def spawn(
        self,
        run_id: uuid.UUID,
        coro_factory: Callable[[asyncio.Event], Coroutine[Any, Any, None]],
    ) -> asyncio.Task[None]:
        """Schedule the coroutine returned by *coro_factory* and register it.

        *coro_factory* receives the run's wake-up :class:`asyncio.Event` so
        the loop can ``await event.wait()`` between steps.
        """
        event = asyncio.Event()
        coro = coro_factory(event)
        task: asyncio.Task[None] = asyncio.create_task(coro, name=f"run-{run_id}")

        record = _SupervisedRun(run_id=run_id, task=task, wake_event=event)
        self._runs[run_id] = record

        task.add_done_callback(lambda t: self._on_task_done(run_id, t))
        return task

    def _on_task_done(self, run_id: uuid.UUID, task: asyncio.Task[Any]) -> None:
        self._runs.pop(run_id, None)
        self._purge_signals_for_run(run_id)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("run-loop task for %s raised", run_id, exc_info=exc)

    async def wake(self, run_id: uuid.UUID) -> None:
        """Signal the run-loop that new state is available.

        No-op if the run is not registered (legitimate on cold restart).
        The loop is responsible for ``event.clear()`` after observing.
        """
        record = self._runs.get(run_id)
        if record is None:
            logger.debug("wake: run %s not registered", run_id)
            return
        record.wake_event.set()

    async def cancel(self, run_id: uuid.UUID) -> None:
        """Request cancellation; await the supervised task's exit."""
        record = self._runs.get(run_id)
        if record is None:
            return
        record.cancel_requested = True
        record.wake_event.set()  # nudge the loop if it's awaiting
        record.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await record.task

    async def await_wake(self, run_id: uuid.UUID) -> None:
        """Block until :meth:`wake` or :meth:`cancel` fires for *run_id*."""
        record = self._runs.get(run_id)
        if record is None:
            return
        await record.wake_event.wait()

    def clear_wake(self, run_id: uuid.UUID) -> None:
        """Reset the wake event; call after observing a wake."""
        record = self._runs.get(run_id)
        if record is not None:
            record.wake_event.clear()

    # -- Operator signals (FEAT-005 / T-096) -------------------------------

    async def await_signal(
        self, run_id: uuid.UUID, name: str, task_id: str
    ) -> dict[str, Any]:
        """Block until :meth:`deliver_signal` matches ``(run_id, name, task_id)``.

        Returns the payload delivered by the matching
        :meth:`deliver_signal` call.  If a signal arrived *before* this
        awaits, the buffered payload is returned immediately and cleared
        (preload semantics).

        Cancellation propagates: if the caller's task is cancelled, the
        event's internal wait raises :class:`asyncio.CancelledError` and
        the caller handles it.
        """
        key = (run_id, name, task_id)
        buffered = self._signal_buffers.pop(key, None)
        if buffered is not None:
            return buffered
        event = self._signal_events.setdefault(key, asyncio.Event())
        try:
            await event.wait()
        finally:
            # Event is single-use per wait; drop it so the next await
            # sees a fresh Event (also cleans up on cancellation).
            self._signal_events.pop(key, None)
        return self._signal_buffers.pop(key, {})

    def deliver_signal(
        self,
        run_id: uuid.UUID,
        name: str,
        task_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Buffer *payload* and wake any in-flight :meth:`await_signal`.

        If no waiter is present, the payload stays buffered until the
        next :meth:`await_signal` call consumes it.  Calling
        :meth:`deliver_signal` twice with the same key before the first
        payload is consumed overwrites the buffer — the endpoint
        guarantees idempotency before this point, so that state is
        impossible in practice.
        """
        key = (run_id, name, task_id)
        self._signal_buffers[key] = payload
        event = self._signal_events.get(key)
        if event is not None:
            event.set()

    def _purge_signals_for_run(self, run_id: uuid.UUID) -> None:
        """Drop any buffered signals / events for *run_id*.  Called on run termination."""
        for key in list(self._signal_buffers):
            if key[0] == run_id:
                self._signal_buffers.pop(key, None)
        for key in list(self._signal_events):
            if key[0] == run_id:
                event = self._signal_events.pop(key, None)
                if event is not None:
                    event.set()  # unblock any lingering waiter

    # -- Inspection --------------------------------------------------------

    def is_registered(self, run_id: uuid.UUID) -> bool:
        return run_id in self._runs

    def is_cancelled(self, run_id: uuid.UUID) -> bool:
        record = self._runs.get(run_id)
        return record is not None and record.cancel_requested

    # -- Shutdown ---------------------------------------------------------

    async def shutdown(self, grace: float = 5.0) -> None:
        """Cancel every outstanding run and await completion.

        Tasks that don't exit within *grace* are force-cancelled.
        """
        async with self._lock:
            records = list(self._runs.values())

        if not records:
            return

        for record in records:
            record.cancel_requested = True
            record.wake_event.set()

        tasks = [r.task for r in records]
        _done, pending = await asyncio.wait(tasks, timeout=grace)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
