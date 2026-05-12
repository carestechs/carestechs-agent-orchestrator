"""FEAT-013 / T-280 — backend-swap safety + remaining edge cases.

Most of the brief's Section 9 cases are already covered elsewhere:

- Concurrent writer + tailing reader → ``test_trace_postgres.py::
  test_open_run_stream_does_not_hold_a_session_across_yields``.
- Non-follow empty stream + clean EOF → ``test_trace_postgres.py::
  test_empty_run_yields_no_rows``.
- ``?since=`` server-side TIMESTAMPTZ parsing → ``test_trace_postgres.py::
  test_since_filter_skips_rows_before_threshold``.
- LISTEN/NOTIFY channel collisions → N/A (Decision 1 ruled out NOTIFY).

This file picks up the one case left: **backend-swap safety** —
starting a run under one backend and resuming under another does not
crash, because the runtime loop never reads its own past trace.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ai.enums import DispatchMode, RunStatus, StepStatus
from app.modules.ai.models import EffectorCall, ExecutorCall, Run, Step
from app.modules.ai.schemas import (
    EffectorCallDto,
    ExecutorCallDto,
    StepDto,
    WebhookEventDto,
)
from app.modules.ai.trace_jsonl import JsonlTraceStore
from app.modules.ai.trace_postgres import PostgresTraceStore


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest_asyncio.fixture(loop_scope="function")
async def seeded_run(session_factory: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Run(
                id=run_id,
                agent_ref="test@0.0.1",
                agent_definition_hash="hash",
                intake={},
                status=RunStatus.RUNNING.value,
                started_at=datetime.now(UTC),
                trace_uri=f"/test/{run_id}",
            )
        )
    return run_id


@pytest.mark.asyncio
async def test_swap_jsonl_to_postgres_in_flight_does_not_crash(
    tmp_path: Path,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_run: uuid.UUID,
) -> None:
    """Phase 1: write a step trace via JSONL.  Phase 2: swap to Postgres and
    record an effector_call.  Neither backend reads the other's prior writes
    and the second backend's record_* path completes cleanly.
    """
    jsonl = JsonlTraceStore(tmp_path)
    postgres = PostgresTraceStore(session_factory)

    # Phase 1 — JSONL writer accepts a step trace.  Under the JSONL backend
    # this is a real write to <tmp_path>/<run_id>.jsonl.
    step_dto = StepDto(
        id=uuid.uuid4(),
        step_number=1,
        node_name="node-A",
        status=StepStatus.COMPLETED,
        node_inputs={},
    )
    await jsonl.record_step(seeded_run, step_dto)
    assert (tmp_path / f"{seeded_run}.jsonl").is_file()

    # Phase 2 — Postgres backend takes over.  record_step is a no-op
    # (Decision 2); record_effector_call writes a row.  Neither call
    # crashes despite the JSONL file existing on disk.
    entity_id = uuid.uuid4()
    eff_dto = EffectorCallDto(
        effector_name="checks.post",
        entity_type="task",
        entity_id=entity_id,
        transition="task.T2",
        transition_key="task.T2",
        status="ok",
        duration_ms=10,
        emitted_at=datetime.now(UTC),
    )

    try:
        # No-op record_step under the Postgres backend.
        await postgres.record_step(seeded_run, step_dto)
        # Real write under the Postgres backend.
        await postgres.record_effector_call(entity_id, eff_dto)

        rows = await postgres.read_effector_calls(entity_id)
        assert len(rows) == 1

        # The runtime loop never reads the past trace, so the JSONL file
        # and the Postgres row co-exist without contention.  The cutover
        # tool (T-276) bridges them when an operator wants history
        # unified.
    finally:
        async with session_factory() as session, session.begin():
            await session.execute(
                delete(EffectorCall).where(EffectorCall.entity_id == entity_id)
            )


@pytest.mark.asyncio
async def test_swap_postgres_to_jsonl_in_flight_does_not_crash(
    tmp_path: Path,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_run: uuid.UUID,
) -> None:
    """Reverse direction — Postgres write, then JSONL takes over.  Same
    invariant: the runtime never reads its own past trace; the JSONL
    backend's record_* methods work even though the prior trace lives in
    Postgres.
    """
    postgres = PostgresTraceStore(session_factory)
    jsonl = JsonlTraceStore(tmp_path)

    dispatch_id = uuid.uuid4()
    exec_dto = ExecutorCallDto(
        dispatch_id=dispatch_id,
        run_id=seeded_run,
        executor_ref="review",
        mode=DispatchMode.LOCAL,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        outcome=None,
    )

    try:
        await postgres.record_executor_call(seeded_run, exec_dto)

        # JSONL takes over.  Writing a webhook trace should land on disk.
        wh_dto = WebhookEventDto(
            id=uuid.uuid4(),
            event_type="node_started",  # type: ignore[arg-type]
            engine_run_id="engine-xyz",
            payload={},
            signature_ok=True,
            received_at=datetime.now(UTC),
        )
        await jsonl.record_webhook_event(seeded_run, wh_dto)
        assert (tmp_path / f"{seeded_run}.jsonl").is_file()
    finally:
        async with session_factory() as session, session.begin():
            await session.execute(
                delete(ExecutorCall).where(ExecutorCall.dispatch_id == dispatch_id)
            )


@pytest.mark.asyncio
async def test_postgres_record_for_persisted_kinds_does_not_touch_disk(
    tmp_path: Path,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_run: uuid.UUID,
) -> None:
    """Decision 2 sanity: the Postgres backend's no-op writes for the four
    already-persisted kinds must not silently fall back to JSONL or any
    other side effect.  ``tmp_path`` stays empty.
    """
    postgres = PostgresTraceStore(session_factory)
    step_dto = StepDto(
        id=uuid.uuid4(),
        step_number=1,
        node_name="node-A",
        status=StepStatus.COMPLETED,
        node_inputs={},
    )
    await postgres.record_step(seeded_run, step_dto)
    assert list(tmp_path.iterdir()) == [], "Postgres backend leaked to disk"

    # And no row was inserted into ``steps`` either — the writer that owns
    # that row (the runtime) wasn't called here.
    async with session_factory() as session:
        result = await session.execute(
            Step.__table__.select().where(Step.run_id == seeded_run)
        )
        assert list(result) == []
