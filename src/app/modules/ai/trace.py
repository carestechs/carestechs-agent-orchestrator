"""Trace store protocol and default no-op implementation (AD-5).

The ``TraceStore`` protocol abstracts trace persistence so that the
JSONL writer (v1) and future Postgres writer (v2) are interchangeable.
All trace records are append-only once written.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.modules.ai.schemas import (
    EffectorCallDto,
    ExecutorCallDto,
    PolicyCallDto,
    RunSignalDto,
    StepDto,
    WebhookEventDto,
)

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class TraceStore(Protocol):
    """Append-only store for run trace records.

    Implementations MUST be safe to call concurrently from an async
    context.  All write methods are fire-and-forget from the caller's
    perspective — errors are logged internally and never propagated to
    the runtime loop.
    """

    async def record_step(self, run_id: uuid.UUID, step: StepDto) -> None:
        """Persist a step trace entry."""
        ...

    async def record_policy_call(self, run_id: uuid.UUID, call: PolicyCallDto) -> None:
        """Persist a policy-call trace entry."""
        ...

    async def record_webhook_event(self, run_id: uuid.UUID, event: WebhookEventDto) -> None:
        """Persist a webhook-event trace entry."""
        ...

    async def record_operator_signal(self, run_id: uuid.UUID, signal: RunSignalDto) -> None:
        """Persist an operator-injected signal trace entry (FEAT-005)."""
        ...

    async def record_effector_call(self, entity_id: uuid.UUID, call: EffectorCallDto) -> None:
        """Persist an effector-call trace entry (FEAT-008).

        Keyed on ``entity_id`` (work item or task) rather than run id —
        effectors fire on lifecycle transitions that may have no
        associated run. The JSONL writer stores these under
        ``<trace_dir>/effectors/<entity_id>.jsonl``.
        """
        ...

    async def record_executor_call(self, run_id: uuid.UUID, call: ExecutorCallDto) -> None:
        """Persist an executor-call trace entry (FEAT-009).

        Emitted by the runtime loop when a dispatch reaches a terminal
        state. The JSONL writer stores these under
        ``<trace_dir>/executors/<run_id>.jsonl`` (separate from the
        per-run step/policy trace so the two streams can be tailed
        independently).
        """
        ...

    async def read_effector_calls(self, entity_id: uuid.UUID) -> list[EffectorCallDto]:
        """Replay every effector-call trace for *entity_id* in insertion order.

        Backs the FEAT-008/T-172 invariant-3 check, where the test
        enumerates declared transitions and asserts each one either
        produced an effector_call trace or is ``no_effector``-exempt.
        """
        ...

    async def open_run_stream(
        self, run_id: uuid.UUID
    ) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]:
        """Return an async iterator over all trace entries for *run_id*.

        The iterator yields entries in insertion order.  For the no-op
        store this is always empty.
        """
        ...

    def tail_run_stream(
        self,
        run_id: uuid.UUID,
        *,
        follow: bool = False,
        since: datetime | None = None,
        kinds: frozenset[str] | None = None,
    ) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]:
        """Richer reader driving the streaming endpoint (FEAT-004).

        Non-follow mode yields every committed record once and closes.
        Follow mode keeps polling for new records until the caller breaks
        out of ``async for``.  ``kinds=None`` (or empty) means "all
        kinds"; ``since=None`` means "no lower bound".
        """
        ...


# ---------------------------------------------------------------------------
# No-op implementation (default in v1)
# ---------------------------------------------------------------------------


class NoopTraceStore:
    """Silently discards all trace writes and yields nothing on reads."""

    async def record_step(self, run_id: uuid.UUID, step: StepDto) -> None:
        """Accept and discard a step trace entry."""

    async def record_policy_call(self, run_id: uuid.UUID, call: PolicyCallDto) -> None:
        """Accept and discard a policy-call trace entry."""

    async def record_webhook_event(self, run_id: uuid.UUID, event: WebhookEventDto) -> None:
        """Accept and discard a webhook-event trace entry."""

    async def record_operator_signal(self, run_id: uuid.UUID, signal: RunSignalDto) -> None:
        """Accept and discard an operator-signal trace entry (FEAT-005)."""

    async def record_effector_call(self, entity_id: uuid.UUID, call: EffectorCallDto) -> None:
        """Accept and discard an effector-call trace entry (FEAT-008)."""

    async def record_executor_call(self, run_id: uuid.UUID, call: ExecutorCallDto) -> None:
        """Accept and discard an executor-call trace entry (FEAT-009)."""

    async def read_effector_calls(self, entity_id: uuid.UUID) -> list[EffectorCallDto]:
        """Return an empty list — the noop backend has no data."""
        del entity_id
        return []

    async def open_run_stream(
        self, run_id: uuid.UUID
    ) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]:
        """Return an empty async iterator."""
        return _empty_async_iterator()

    def tail_run_stream(
        self,
        run_id: uuid.UUID,
        *,
        follow: bool = False,
        since: datetime | None = None,
        kinds: frozenset[str] | None = None,
    ) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]:
        """Yield nothing — the noop backend has no data regardless of flags."""
        return _empty_async_iterator()


async def _empty_async_iterator() -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]:
    """Yield nothing — helper for ``NoopTraceStore.open_run_stream``."""
    return
    yield  # makes this an async generator


# ---------------------------------------------------------------------------
# FastAPI dependency factory
# ---------------------------------------------------------------------------

_trace_store: TraceStore | None = None


def get_trace_store() -> TraceStore:
    """Return the active ``TraceStore`` implementation.

    Dispatches on :attr:`~app.config.Settings.trace_backend`:

    * ``"noop"`` — :class:`NoopTraceStore` (default for most tests).
    * ``"jsonl"`` — :class:`~app.modules.ai.trace_jsonl.JsonlTraceStore`
      writing to :attr:`~app.config.Settings.trace_dir` (AD-5 v1).

    Override in tests via ``app.dependency_overrides[get_trace_store]``.
    """
    global _trace_store
    if _trace_store is not None:
        return _trace_store

    from app.config import get_settings

    settings = get_settings()
    if settings.trace_backend == "jsonl":
        from app.modules.ai.trace_jsonl import JsonlTraceStore

        _trace_store = JsonlTraceStore(settings.trace_dir)
    else:
        _trace_store = NoopTraceStore()
    return _trace_store


def _reset_trace_store_cache() -> None:
    """Test hook — clear the cached singleton so the next call rebuilds it.

    Imported by tests; see ``tests/modules/ai/test_trace_jsonl.py``.
    """
    global _trace_store
    _trace_store = None


__all__ = ["NoopTraceStore", "TraceStore", "_reset_trace_store_cache", "get_trace_store"]
