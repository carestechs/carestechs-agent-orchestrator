"""Agent runtime loop, trace emission, stop conditions.

Control-plane functions raise ``NotImplementedYet`` until FEAT-002 lands.
``ingest_engine_event`` is fully implemented per T-013 — the webhook
endpoint is load-bearing on day one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import jsonschema
from jsonschema import ValidationError as JsonSchemaValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.modules.ai import repository
from app.modules.ai.agents import list_agent_records, load_agent
from app.modules.ai.enums import RunStatus, StepStatus, StopReason, WebhookEventType
from app.modules.ai.models import Run, RunMemory, Step, WebhookEvent, generate_uuid7
from app.modules.ai.reconciliation import next_step_state
from app.modules.ai.runtime import run_loop
from app.modules.ai.schemas import (
    AgentDto,
    CancelRunRequest,
    CreateRunRequest,
    LastStepSummary,
    PolicyCallDto,
    RunDetailDto,
    RunSignalDto,
    RunSummaryDto,
    StepDto,
    WebhookEventDto,
)

if TYPE_CHECKING:
    from app.config import Settings
    from app.core.llm import LLMProvider
    from app.modules.ai.engine_client import FlowEngineClient
    from app.modules.ai.supervisor import RunSupervisor
    from app.modules.ai.trace import TraceStore

logger = logging.getLogger(__name__)


_TAIL_POLL_SECONDS = 0.2
"""Cadence at which :func:`stream_trace` polls the trace-store iterator
AND re-reads ``Run.status`` when deciding whether to close the stream.
Module-level so tests monkeypatch it to ~0.01 for speed."""


_KIND_BY_TYPE: dict[
    type[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto], str
] = {
    StepDto: "step",
    PolicyCallDto: "policy_call",
    WebhookEventDto: "webhook_event",
    RunSignalDto: "operator_signal",
}


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


async def start_run(
    request: CreateRunRequest,
    *,
    settings: Settings,
    supervisor: RunSupervisor,
    session_factory: async_sessionmaker[AsyncSession],
    policy: LLMProvider,
    engine: FlowEngineClient,
    trace: TraceStore,
) -> RunSummaryDto:
    """Start a new agent run.  Returns immediately with the run summary.

    AD-2 compliance: this function completes well before the loop finishes.
    The supervised task is spawned into the asyncio event loop after the
    DB commit, then the DTO is returned.
    """
    # 1. Resolve the agent (raises NotFoundError if missing).
    agent = load_agent(request.agent_ref, settings.agents_dir)
    assert agent.agent_definition_hash is not None  # loader always sets it

    # 2. Validate intake against the agent's declared schema.
    if agent.intake_schema and agent.intake_schema.get("properties"):
        try:
            jsonschema.validate(instance=request.intake, schema=agent.intake_schema)
        except JsonSchemaValidationError as exc:
            raise ValidationError(
                detail=f"intake validation failed: {exc.message}",
                errors={"intake": [exc.message]},
            ) from exc

    # 3. Persist Run + RunMemory in one commit.
    run_id = generate_uuid7()
    trace_uri = f"file://{settings.trace_dir}/{run_id}.jsonl"
    run = Run(
        id=run_id,
        agent_ref=request.agent_ref,
        agent_definition_hash=agent.agent_definition_hash,
        intake=request.intake,
        status=RunStatus.PENDING,
        started_at=datetime.now(UTC),
        trace_uri=trace_uri,
    )
    memory = RunMemory(run_id=run_id, data={})

    async with session_factory() as session:
        session.add_all([run, memory])
        await session.commit()
        await session.refresh(run)

    # 4. Spawn the supervised loop; the request returns immediately after.
    def _factory(event: asyncio.Event) -> Any:
        return run_loop(
            run_id=run_id,
            agent=agent,
            policy=policy,
            engine=engine,
            trace=trace,
            supervisor=supervisor,
            session_factory=session_factory,
            cancel_event=event,
        )

    supervisor.spawn(run_id, _factory)

    return RunSummaryDto.model_validate(run, from_attributes=True)


async def list_runs(
    db: AsyncSession,
    *,
    status: str | None = None,
    agent_ref: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[RunSummaryDto], int]:
    """Return paginated runs and total count."""
    page = max(page, 1)
    page_size = max(1, min(page_size, 100))

    total = await repository.count_runs(db, status=status, agent_ref=agent_ref)
    rows = await repository.select_runs(
        db,
        status=status,
        agent_ref=agent_ref,
        page=page,
        page_size=page_size,
    )
    items = [RunSummaryDto.model_validate(r, from_attributes=True) for r in rows]
    return items, total


async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession,
) -> RunDetailDto:
    """Fetch a single run with step summary."""
    run = await repository.get_run_by_id(db, run_id)
    if run is None:
        raise NotFoundError(f"run not found: {run_id}")

    step_count = await repository.count_steps(db, run_id)
    last = await repository.latest_step(db, run_id)
    last_dto = (
        LastStepSummary.model_validate(last, from_attributes=True)
        if last is not None
        else None
    )

    return RunDetailDto(
        id=run.id,
        agent_ref=run.agent_ref,
        agent_definition_hash=run.agent_definition_hash,
        intake=run.intake,
        status=RunStatus(run.status),
        stop_reason=run.stop_reason,  # pyright: ignore[reportArgumentType]
        started_at=run.started_at,
        ended_at=run.ended_at,
        trace_uri=run.trace_uri,
        step_count=step_count,
        last_step=last_dto,
    )


_TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}


async def cancel_run(
    run_id: uuid.UUID,
    request: CancelRunRequest,
    db: AsyncSession,
    *,
    supervisor: RunSupervisor,
) -> RunSummaryDto:
    """Cancel a running run.

    DB-first: we flip ``status=cancelled`` in one commit BEFORE calling
    :meth:`RunSupervisor.cancel`, so a concurrent webhook cannot "revive"
    the run by racing with the supervisor's cancel.
    """
    run = await repository.get_run_by_id(db, run_id)
    if run is None:
        raise NotFoundError(f"run not found: {run_id}")

    if RunStatus(run.status) in _TERMINAL_STATUSES:
        # Idempotent no-op: already terminal, nothing to cancel.
        return RunSummaryDto.model_validate(run, from_attributes=True)

    existing_state: dict[str, Any] = dict(run.final_state or {})
    existing_state["cancel_reason"] = request.reason
    existing_state["cancelled_via"] = "api"

    run.status = RunStatus.CANCELLED
    run.stop_reason = StopReason.CANCELLED
    run.final_state = existing_state
    run.ended_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(run)

    await supervisor.cancel(run_id)

    return RunSummaryDto.model_validate(run, from_attributes=True)


# ---------------------------------------------------------------------------
# Operator signals (FEAT-005 / T-098)
# ---------------------------------------------------------------------------


async def send_signal(
    *,
    run_id: uuid.UUID,
    name: str,
    task_id: str,
    payload: dict[str, Any],
    db: AsyncSession,
    supervisor: RunSupervisor,
    trace: TraceStore,
) -> tuple[RunSignalDto, bool]:
    """Persist an operator-injected signal, then wake the runtime loop.

    Returns ``(dto, created)``.  ``created=False`` means the signal was
    already received (idempotent match on ``(run_id, name, task_id)``) —
    the supervisor is NOT re-woken in that case.

    Raises :class:`NotFoundError` when the run is unknown or the signal
    targets a task not in the run's memory.  Raises
    :class:`ConflictError` when the run is already terminal.
    """
    run = await repository.get_run_by_id(db, run_id)
    if run is None:
        raise NotFoundError(f"run not found: {run_id}")
    if RunStatus(run.status) in _TERMINAL_STATUSES:
        raise ConflictError(f"run already terminal: {run.status}")

    memory_row = await db.scalar(
        select(RunMemory).where(RunMemory.run_id == run_id)
    )
    memory_data: dict[str, Any] = (memory_row.data if memory_row is not None else {}) or {}
    tasks_raw: Any = memory_data.get("tasks") or []
    known_task_ids: set[str] = set()
    if isinstance(tasks_raw, list):
        for t in tasks_raw:  # type: ignore[assignment]
            if isinstance(t, dict):
                raw_id = t.get("id")  # type: ignore[attr-defined]
                if isinstance(raw_id, str):
                    known_task_ids.add(raw_id)
    if task_id not in known_task_ids:
        raise NotFoundError(f"task not found in run: {task_id}")

    dedupe_key = repository.compute_signal_dedupe_key(run_id, name, task_id)
    row, created = await repository.create_run_signal(
        db,
        run_id=run_id,
        name=name,
        task_id=task_id,
        payload=payload,
        dedupe_key=dedupe_key,
    )
    await db.commit()
    dto = RunSignalDto.model_validate(row, from_attributes=True)

    if created:
        try:
            await trace.record_operator_signal(run_id, dto)
        except Exception:
            logger.warning("trace write failed for operator signal", exc_info=True)
        supervisor.deliver_signal(run_id, name, task_id, payload)

    return dto, created


# ---------------------------------------------------------------------------
# Steps / Policy calls
# ---------------------------------------------------------------------------


async def list_steps(
    run_id: uuid.UUID,
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[StepDto], int]:
    """Return paginated steps for a run."""
    page = max(page, 1)
    page_size = max(1, min(page_size, 100))

    if await repository.get_run_by_id(db, run_id) is None:
        raise NotFoundError(f"run not found: {run_id}")

    total = await repository.count_steps(db, run_id)
    rows = await repository.select_steps(db, run_id, page=page, page_size=page_size)
    items = [StepDto.model_validate(r, from_attributes=True) for r in rows]
    return items, total


async def list_policy_calls(
    run_id: uuid.UUID,
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[PolicyCallDto], int]:
    """Return paginated policy calls for a run."""
    page = max(page, 1)
    page_size = max(1, min(page_size, 100))

    if await repository.get_run_by_id(db, run_id) is None:
        raise NotFoundError(f"run not found: {run_id}")

    total = await repository.count_policy_calls(db, run_id)
    rows = await repository.select_policy_calls(
        db, run_id, page=page, page_size=page_size
    )
    items = [PolicyCallDto.model_validate(r, from_attributes=True) for r in rows]
    return items, total


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


async def stream_trace(
    run_id: uuid.UUID,
    *,
    db: AsyncSession,
    trace: TraceStore,
    follow: bool = False,
    since: datetime | None = None,
    kinds: frozenset[str] | None = None,
) -> AsyncIterator[str]:
    """Yield the run's trace as NDJSON lines (one line per record).

    Non-follow mode yields every record already on disk, then closes.
    Follow mode keeps streaming until the run reaches a terminal state
    AND the reader has been idle for two consecutive
    :data:`_TAIL_POLL_SECONDS`-bounded polls.
    """
    run = await repository.get_run_by_id(db, run_id)
    if run is None:
        raise NotFoundError(f"run not found: {run_id}")

    iterator = trace.tail_run_stream(
        run_id, follow=follow, since=since, kinds=kinds
    )

    if not follow:
        async for dto in iterator:
            yield _serialize_trace_record(dto)
        return

    # Follow mode: run the tail iterator in a background task that
    # forwards each DTO to a queue.  The main coroutine drains the queue
    # and periodically re-checks ``Run.status`` to decide when to close.
    # Separating the two keeps us from cancelling the aiofiles-backed
    # iterator mid-``readline`` (which leaves it in a half-read state).
    queue: asyncio.Queue[
        StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto | None
    ] = asyncio.Queue()
    reader = asyncio.create_task(_drain_iterator(iterator, queue))
    try:
        empty_polls = 0
        while True:
            try:
                dto = await asyncio.wait_for(
                    queue.get(), timeout=_TAIL_POLL_SECONDS
                )
            except TimeoutError:
                await db.refresh(run)
                if RunStatus(run.status) in _TERMINAL_STATUSES:
                    if empty_polls >= 1:
                        return
                    empty_polls += 1
                else:
                    empty_polls = 0
                continue

            if dto is None:
                # Reader finished (writer closed, non-follow behavior).
                return
            yield _serialize_trace_record(dto)
            empty_polls = 0
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader


async def _drain_iterator(
    iterator: AsyncIterator[StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto],
    queue: asyncio.Queue[
        StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto | None
    ],
) -> None:
    """Forward every DTO from *iterator* into *queue*; enqueue ``None`` on end."""
    try:
        async for dto in iterator:
            await queue.put(dto)
    finally:
        await queue.put(None)


def _serialize_trace_record(
    dto: StepDto | PolicyCallDto | WebhookEventDto | RunSignalDto,
) -> str:
    """Render a trace DTO as a single NDJSON line ending in ``\\n``."""
    kind = _KIND_BY_TYPE[type(dto)]
    return (
        json.dumps(
            {"kind": kind, "data": dto.model_dump(mode="json", by_alias=True)}
        )
        + "\n"
    )


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


async def list_agents(*, settings: Settings) -> list[AgentDto]:
    """List agent definitions found on disk."""
    records = list_agent_records(settings.agents_dir)
    items: list[AgentDto] = []
    for rec in records:
        agent = rec.definition
        assert agent.agent_definition_hash is not None
        items.append(
            AgentDto(
                ref=f"{agent.ref}@{agent.version}",
                definition_hash=agent.agent_definition_hash,
                path=str(rec.path),
                intake_schema=agent.intake_schema,
                available_nodes=[n.name for n in agent.nodes],
            )
        )
    return items


# ---------------------------------------------------------------------------
# Webhook ingestion
# ---------------------------------------------------------------------------


async def _persist_event(
    event_body: dict[str, Any],
    signature_ok: bool,
    db: AsyncSession,
) -> tuple[WebhookEvent, bool]:
    """Persist a webhook event; return ``(record, is_new)``.

    Looks up the target ``Step`` by ``engine_run_id``; raises
    :class:`NotFoundError` if none exists (FK would violate).  Idempotent on
    ``dedupe_key`` — a duplicate returns the existing row with ``is_new=False``.
    """
    engine_run_id: str = event_body["engine_run_id"]
    dedupe_key: str = event_body["engine_event_id"]
    event_type: str = event_body["event_type"]
    payload: dict[str, Any] = event_body.get("payload", {}) or {}

    existing = await db.scalar(
        select(WebhookEvent).where(WebhookEvent.dedupe_key == dedupe_key)
    )
    if existing is not None:
        return existing, False

    step = await db.scalar(select(Step).where(Step.engine_run_id == engine_run_id))
    if step is None:
        logger.warning(
            "webhook event for unknown engine_run_id",
            extra={"engine_run_id": engine_run_id, "signature_ok": signature_ok},
        )
        raise NotFoundError(f"unknown engine_run_id: {engine_run_id}")

    record = WebhookEvent(
        run_id=step.run_id,
        step_id=step.id,
        event_type=event_type,
        engine_run_id=engine_run_id,
        payload=payload,
        signature_ok=signature_ok,
        dedupe_key=dedupe_key,
    )
    db.add(record)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        winner = await db.scalar(
            select(WebhookEvent).where(WebhookEvent.dedupe_key == dedupe_key)
        )
        if winner is None:  # pragma: no cover — unexpected constraint violation
            raise
        return winner, False

    await db.refresh(record)
    return record, True


async def _reconcile_step_from_event(
    event: WebhookEvent,
    db: AsyncSession,
) -> bool:
    """Apply the step-level state transition encoded by *event*.

    Returns ``True`` if the step's status actually changed.  Skips the
    update entirely when the owning :class:`Run` is already terminal (late
    event after cancel/budget).
    """
    step = await db.scalar(select(Step).where(Step.id == event.step_id))
    if step is None:
        return False

    run = await db.scalar(select(Run).where(Run.id == step.run_id))
    if run is None:
        return False
    if run.status in {"completed", "failed", "cancelled"}:
        return False

    try:
        event_type = WebhookEventType(event.event_type)
    except ValueError:
        return False

    current = StepStatus(step.status)
    new_status, changed = next_step_state(current, event_type)
    if not changed:
        return False

    step.status = new_status
    payload: dict[str, Any] = event.payload or {}
    if new_status is StepStatus.COMPLETED:
        result = payload.get("result")
        step.node_result = result if result is not None else payload
        step.completed_at = datetime.now(UTC)
    elif new_status is StepStatus.FAILED:
        error = payload.get("error")
        step.error = error if error is not None else {"payload": payload}
        step.completed_at = datetime.now(UTC)

    await db.commit()
    return True


async def ingest_engine_event(
    event_body: dict[str, Any],
    signature_ok: bool,
    db: AsyncSession,
    supervisor: RunSupervisor | None = None,
    trace: TraceStore | None = None,
) -> WebhookEventDto:
    """Persist a webhook event, reconcile the step, wake the loop.

    1. Persist via :func:`_persist_event` (idempotent on ``dedupe_key``).
    2. If the event is new AND the signature is valid, reconcile the step
       state machine and wake the owning run-loop coroutine.
    3. Write the trace line (best-effort; logged on failure).
    """
    record, is_new = await _persist_event(event_body, signature_ok, db)
    dto = WebhookEventDto.model_validate(record, from_attributes=True)

    if is_new and signature_ok:
        await _reconcile_step_from_event(record, db)
        if supervisor is not None:
            await supervisor.wake(record.run_id)

    if is_new and trace is not None:
        try:
            await trace.record_webhook_event(record.run_id, dto)
        except Exception:
            logger.warning("trace write failed for webhook event", exc_info=True)

    return dto
