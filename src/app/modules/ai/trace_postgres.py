"""Postgres implementation of :class:`~app.modules.ai.trace.TraceStore` (AD-5 v2 / FEAT-013).

Design contract pinned in
``docs/design/feat-013-trace-tail-mechanism.md``:

Decision 1 — Tail mechanism is polling-only at 200 ms.  No
``LISTEN``/``NOTIFY``.

Decision 2 — Writes for ``step`` / ``policy_call`` / ``webhook_event`` /
``operator_signal`` are **no-ops**: those rows are already persisted by
the runtime / signal adapter that owns them.  The reader reconstructs
them by joining the existing tables.  Double-writing here would
re-create the FEAT-008-shaped drift pattern.

Decision 3 — ``open_run_stream`` reads from four sources (``steps`` /
``policy_calls`` / ``webhook_events`` / ``run_signals``).  Effector
calls and executor calls have their own protocol methods and are not
part of the per-run stream union — same surface as ``JsonlTraceStore``.

Every read path opens a fresh ``AsyncSession`` per chunk.  No session is
held across iterator boundaries (matches the runtime-loop discipline in
CLAUDE.md).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.ai.enums import RunStatus
from app.modules.ai.models import (
    EffectorCall,
    ExecutorCall,
    PolicyCall,
    Run,
    RunSignal,
    Step,
    WebhookEvent,
)
from app.modules.ai.schemas import (
    EffectorCallDto,
    ExecutorCallDto,
    PolicyCallDto,
    RunSignalDto,
    StepDto,
    WebhookEventDto,
)

logger = logging.getLogger(__name__)


_TAIL_POLL_SECONDS = 0.2
"""Polling cadence for follow-mode tail (Decision 1)."""

_CHUNK_SIZE = 1000
"""Per-branch row chunk size for one-shot reads."""

# Kinds that participate in ``open_run_stream`` / ``tail_run_stream``.
# Matches the protocol return type and JSONL behavior.
_RUN_STREAM_KINDS: frozenset[str] = frozenset({"step", "policy_call", "webhook_event", "operator_signal"})


# ---------------------------------------------------------------------------
# Row → DTO projections
# ---------------------------------------------------------------------------


def _step_to_dto(row: Step) -> StepDto:
    return StepDto(
        id=row.id,
        step_number=row.step_number,
        node_name=row.node_name,
        status=row.status,  # type: ignore[arg-type]
        node_inputs=row.node_inputs,
        node_result=row.node_result,
        error=row.error,
        dispatched_at=row.dispatched_at,
        completed_at=row.completed_at,
    )


def _policy_call_to_dto(row: PolicyCall) -> PolicyCallDto:
    return PolicyCallDto(
        id=row.id,
        step_id=row.step_id,
        provider=row.provider,
        model=row.model,
        selected_tool=row.selected_tool,
        tool_arguments=row.tool_arguments,
        available_tools=row.available_tools,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        latency_ms=row.latency_ms,
        created_at=row.created_at,
    )


def _webhook_event_to_dto(row: WebhookEvent) -> WebhookEventDto:
    return WebhookEventDto(
        id=row.id,
        event_type=row.event_type,  # type: ignore[arg-type]
        engine_run_id=row.engine_run_id,
        payload=row.payload,
        signature_ok=row.signature_ok,
        source=row.source,  # type: ignore[arg-type]
        received_at=row.received_at,
        processed_at=row.processed_at,
    )


def _run_signal_to_dto(row: RunSignal) -> RunSignalDto:
    return RunSignalDto(
        id=row.id,
        run_id=row.run_id,
        name=row.name,
        task_id=row.task_id,
        payload=row.payload,
        received_at=row.received_at,
        dedupe_key=row.dedupe_key,
    )


def _effector_call_to_dto(row: EffectorCall) -> EffectorCallDto:
    # The ``payload`` JSON carries the DTO body verbatim — re-validate so the
    # reader emits the same shape the writer accepted.
    return EffectorCallDto.model_validate(row.payload)


# ---------------------------------------------------------------------------
# Sort keys for the union ordering (Decision 3)
# ---------------------------------------------------------------------------


def _step_sort_key(row: Step) -> tuple[datetime, int, str]:
    # Steps use ``created_at`` as their canonical trace timestamp.  Tie-break
    # on the row's ``id`` (UUIDv7 — also time-sortable, but lexically
    # comparable for deterministic ordering).
    return (row.created_at, 0, str(row.id))


def _policy_call_sort_key(row: PolicyCall) -> tuple[datetime, int, str]:
    return (row.created_at, 1, str(row.id))


def _webhook_event_sort_key(row: WebhookEvent) -> tuple[datetime, int, str]:
    # ``received_at`` is the engine-side wall-clock; matches the JSONL
    # backend's ``_record_timestamp`` for this DTO.
    return (row.received_at, 2, str(row.id))


def _run_signal_sort_key(row: RunSignal) -> tuple[datetime, int, str]:
    return (row.received_at, 3, str(row.id))


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class PostgresTraceStore:
    """Backs the ``TraceStore`` protocol with the Postgres tables.

    Constructed with the same ``async_sessionmaker`` the rest of the app
    uses — no new engine, no new pool.  Each method opens its own
    short-lived session.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # -- Writers: the four already-persisted kinds are NO-OPs --------------
    #
    # The rows live in ``steps`` / ``policy_calls`` / ``webhook_events`` /
    # ``run_signals`` and are written by the runtime / signal adapter that
    # owns them.  Writing again here would double-record — see Decision 2.

    async def record_step(self, run_id: uuid.UUID, step: StepDto) -> None:
        """No-op.  ``steps`` row written by runtime / runtime_deterministic."""
        del run_id, step

    async def record_policy_call(self, run_id: uuid.UUID, call: PolicyCallDto) -> None:
        """No-op.  ``policy_calls`` row written by the LLM-policy runtime loop."""
        del run_id, call

    async def record_webhook_event(self, run_id: uuid.UUID, event: WebhookEventDto) -> None:
        """No-op.  ``webhook_events`` row written by the webhook router."""
        del run_id, event

    async def record_operator_signal(self, run_id: uuid.UUID, signal: RunSignalDto) -> None:
        """No-op.  ``run_signals`` row written by the signal-create adapter."""
        del run_id, signal

    # -- Writers: the two FEAT-013 tables ---------------------------------

    async def record_effector_call(self, entity_id: uuid.UUID, call: EffectorCallDto) -> None:
        async with self._session_factory() as session:
            row = EffectorCall(
                entity_id=entity_id,
                transition_key=call.transition_key,
                payload=call.model_dump(mode="json", by_alias=True),
                outcome=call.status,
            )
            session.add(row)
            await session.commit()

    async def record_executor_call(self, run_id: uuid.UUID, call: ExecutorCallDto) -> None:
        async with self._session_factory() as session:
            row = ExecutorCall(
                run_id=run_id,
                node_name=call.executor_ref,
                dispatch_id=call.dispatch_id,
                payload=call.model_dump(mode="json", by_alias=True),
                outcome=(call.outcome.value if call.outcome is not None else "pending"),
            )
            session.add(row)
            await session.commit()

    # -- Readers ----------------------------------------------------------

    async def read_effector_calls(self, entity_id: uuid.UUID) -> list[EffectorCallDto]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(EffectorCall)
                .where(EffectorCall.entity_id == entity_id)
                .order_by(EffectorCall.created_at.asc(), EffectorCall.id.asc())
            )
            return [_effector_call_to_dto(r) for r in result.scalars().all()]

    async def open_run_stream(
        self, run_id: uuid.UUID
    ) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]:
        """One-shot drain of every trace row for *run_id*, ordered by event time."""
        return self._drain(run_id, since=None, kinds=None)

    def tail_run_stream(
        self,
        run_id: uuid.UUID,
        *,
        follow: bool = False,
        since: datetime | None = None,
        kinds: frozenset[str] | None = None,
    ) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]:
        effective_kinds = kinds if kinds else None
        return self._tail(run_id, follow=follow, since=since, kinds=effective_kinds)

    # -- Implementation --------------------------------------------------

    async def _drain(
        self,
        run_id: uuid.UUID,
        *,
        since: datetime | None,
        kinds: frozenset[str] | None,
    ) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]:
        """Drain every row past *since*, ordered, in one short-lived session."""
        async with self._session_factory() as session:
            rows = await self._fetch_all(session, run_id, since=since, kinds=kinds)
            for dto in rows:
                yield dto

    async def _tail(
        self,
        run_id: uuid.UUID,
        *,
        follow: bool,
        since: datetime | None,
        kinds: frozenset[str] | None,
    ) -> AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]:
        """Tail reader (polling) — backlog drain, then 200 ms poll loop.

        Each fetch opens a fresh session; no session is held across the
        poll sleep.  Per-source high-water marks (one per table) drive
        the filter pushdown so the next poll never re-reads a row
        already yielded — and the SQL filter is on the same column the
        watermark tracks (no DTO-vs-column mismatch).
        """
        watermarks: dict[str, datetime] = {}
        if since is not None:
            watermarks = {
                "step": since,
                "policy_call": since,
                "webhook_event": since,
                "operator_signal": since,
            }

        while True:
            async with self._session_factory() as session:
                batch, new_watermarks = await self._fetch_with_watermarks(
                    session,
                    run_id,
                    watermarks=watermarks,
                    kinds=kinds,
                )
            watermarks = new_watermarks
            for dto in batch:
                yield dto

            if not follow:
                return

            # Terminal-state poll — re-read in its own session so we never
            # hold one open across the sleep.
            async with self._session_factory() as session:
                terminal = await self._run_is_terminal(session, run_id)
            if terminal:
                # One last drain to catch any rows committed after our last
                # fetch but before the run row flipped.
                async with self._session_factory() as session:
                    final, _ = await self._fetch_with_watermarks(
                        session,
                        run_id,
                        watermarks=watermarks,
                        kinds=kinds,
                    )
                for dto in final:
                    yield dto
                return

            await asyncio.sleep(_TAIL_POLL_SECONDS)

    async def _fetch_all(
        self,
        session: AsyncSession,
        run_id: uuid.UUID,
        *,
        since: datetime | None,
        kinds: frozenset[str] | None,
    ) -> list[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto]:
        """Execute the four branch queries and merge-sort their outputs."""
        watermarks: dict[str, datetime] = {}
        if since is not None:
            watermarks = {k: since for k in _RUN_STREAM_KINDS}
        merged, _ = await self._fetch_with_watermarks(session, run_id, watermarks=watermarks, kinds=kinds)
        return merged

    async def _fetch_with_watermarks(
        self,
        session: AsyncSession,
        run_id: uuid.UUID,
        *,
        watermarks: dict[str, datetime],
        kinds: frozenset[str] | None,
    ) -> tuple[
        list[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto],
        dict[str, datetime],
    ]:
        """Per-source filter + per-source watermark advance.

        The watermark for each source is the canonical timestamp column
        that source uses both for ordering and for ``since``-filter
        pushdown — ``created_at`` for ``steps``/``policy_calls``,
        ``received_at`` for ``webhook_events``/``run_signals``.  The
        next-poll filter uses the same column.
        """
        active = _RUN_STREAM_KINDS if kinds is None else (_RUN_STREAM_KINDS & kinds)
        if not active:
            return [], dict(watermarks)

        new_watermarks = dict(watermarks)
        merged: list[
            tuple[
                tuple[datetime, int, str],
                StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto,
            ]
        ] = []

        if "step" in active:
            stmt = (
                select(Step)
                .where(Step.run_id == run_id)
                .order_by(Step.created_at.asc(), Step.id.asc())
                .limit(_CHUNK_SIZE)
            )
            wm = watermarks.get("step")
            if wm is not None:
                stmt = stmt.where(Step.created_at > wm)
            result = await session.execute(stmt)
            for row in result.scalars().all():
                merged.append((_step_sort_key(row), _step_to_dto(row)))
                new_watermarks["step"] = row.created_at

        if "policy_call" in active:
            stmt = (
                select(PolicyCall)
                .where(PolicyCall.run_id == run_id)
                .order_by(PolicyCall.created_at.asc(), PolicyCall.id.asc())
                .limit(_CHUNK_SIZE)
            )
            wm = watermarks.get("policy_call")
            if wm is not None:
                stmt = stmt.where(PolicyCall.created_at > wm)
            result = await session.execute(stmt)
            for row in result.scalars().all():
                merged.append((_policy_call_sort_key(row), _policy_call_to_dto(row)))
                new_watermarks["policy_call"] = row.created_at

        if "webhook_event" in active:
            stmt = (
                select(WebhookEvent)
                .where(WebhookEvent.run_id == run_id)
                .order_by(WebhookEvent.received_at.asc(), WebhookEvent.id.asc())
                .limit(_CHUNK_SIZE)
            )
            wm = watermarks.get("webhook_event")
            if wm is not None:
                stmt = stmt.where(WebhookEvent.received_at > wm)
            result = await session.execute(stmt)
            for row in result.scalars().all():
                merged.append((_webhook_event_sort_key(row), _webhook_event_to_dto(row)))
                new_watermarks["webhook_event"] = row.received_at

        if "operator_signal" in active:
            stmt = (
                select(RunSignal)
                .where(RunSignal.run_id == run_id)
                .order_by(RunSignal.received_at.asc(), RunSignal.id.asc())
                .limit(_CHUNK_SIZE)
            )
            wm = watermarks.get("operator_signal")
            if wm is not None:
                stmt = stmt.where(RunSignal.received_at > wm)
            result = await session.execute(stmt)
            for row in result.scalars().all():
                merged.append((_run_signal_sort_key(row), _run_signal_to_dto(row)))
                new_watermarks["operator_signal"] = row.received_at

        # Stable merge sort by (timestamp, source-tag, id-str).  Chunk
        # size is bounded by ``_CHUNK_SIZE`` * 4 = 4000 rows per fetch.
        merged.sort(key=lambda pair: pair[0])
        return [dto for _, dto in merged], new_watermarks

    async def _run_is_terminal(self, session: AsyncSession, run_id: uuid.UUID) -> bool:
        """Return ``True`` if the run has reached a terminal status."""
        result = await session.execute(select(Run.status).where(Run.id == run_id))
        status = result.scalar_one_or_none()
        if status is None:
            # Run has not been written yet — not terminal, keep polling.
            return False
        return status in {
            RunStatus.COMPLETED.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
        }


__all__ = ["PostgresTraceStore"]
