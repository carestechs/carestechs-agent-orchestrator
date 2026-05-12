"""FEAT-013 / T-278 — byte-identical NDJSON parity across backends.

The contract from AC-3:

> For a run executed end-to-end under ``trace_backend = "postgres"``,
> ``GET /api/v1/runs/{id}/trace`` returns byte-identical NDJSON to the
> same run replayed under ``trace_backend = "jsonl"``, after normalizing
> only the ``created_at`` timestamp resolution differences (TIMESTAMPTZ
> vs. ISO-string round-trip).

This test stages identical logical state in both backends and compares
the output of ``tail_run_stream`` byte-for-byte.  Equivalent state means:

- For each of the four ``open_run_stream`` kinds (``step`` /
  ``policy_call`` / ``webhook_event`` / ``operator_signal``), the same
  DTO is presented to the JSONL writer via ``record_*`` AND inserted
  into the matching SQL table (matches the production runtime, which
  writes both in the same handler).
- The same UUIDs and timestamps are used on both sides — no
  randomization.

The serializer ``_serialize_trace_record`` is identical regardless of
backend (one canonical helper in ``modules/ai/service.py``), so any
divergence collapses to "the readers returned different DTOs" — which
this test surfaces directly with a diff.

**Scope note:** the brief's parity bar is for the "one terminal write
per kind" shape, which matches the FEAT-009/FEAT-011 deterministic
runtime.  The legacy LLM-policy runtime calls ``record_step`` more
than once per logical step (dispatched → completed); JSONL appends a
line per call while Postgres collapses to one row.  That divergence is
documented in the design doc and is not in this test's scope.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ai.enums import (
    RunStatus,
    StepStatus,
    WebhookEventType,
    WebhookSource,
)
from app.modules.ai.models import (
    PolicyCall,
    Run,
    RunSignal,
    Step,
    WebhookEvent,
)
from app.modules.ai.schemas import (
    PolicyCallDto,
    RunSignalDto,
    StepDto,
    WebhookEventDto,
)
from app.modules.ai.service import _serialize_trace_record
from app.modules.ai.trace_jsonl import JsonlTraceStore
from app.modules.ai.trace_postgres import PostgresTraceStore


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest_asyncio.fixture(loop_scope="function")
async def parity_run(session_factory: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    """Insert a fresh Run and clean up the staged trace rows afterwards."""
    run_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Run(
                id=run_id,
                agent_ref="test@0.0.1",
                agent_definition_hash="hash",
                intake={},
                status=RunStatus.COMPLETED.value,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
                trace_uri=f"/test/{run_id}",
            )
        )

    yield run_id

    async with session_factory() as session, session.begin():
        await session.execute(delete(RunSignal).where(RunSignal.run_id == run_id))
        await session.execute(delete(WebhookEvent).where(WebhookEvent.run_id == run_id))
        await session.execute(delete(PolicyCall).where(PolicyCall.run_id == run_id))
        await session.execute(delete(Step).where(Step.run_id == run_id))
        await session.execute(delete(Run).where(Run.id == run_id))


async def _stage_identical_state(
    *,
    run_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession],
    jsonl: JsonlTraceStore,
) -> dict[str, datetime]:
    """Seed one row of each kind in SQL AND one line of each kind in JSONL.

    Returns the timestamps used per kind so the test can verify them.
    """
    base = datetime(2026, 5, 11, 12, 0, 0, 0, tzinfo=UTC)

    # Step — ascending creation order
    step_id = uuid.uuid4()
    step_dto = StepDto(
        id=step_id,
        step_number=1,
        node_name="node-A",
        status=StepStatus.COMPLETED,
        node_inputs={"k": "v"},
        node_result={"ok": True},
        error=None,
        dispatched_at=None,
        completed_at=base,
    )

    pc_id = uuid.uuid4()
    pc_dto = PolicyCallDto(
        id=pc_id,
        step_id=step_id,
        provider="stub",
        model="stub-1",
        selected_tool="terminate",
        tool_arguments={},
        available_tools=[],
        input_tokens=0,
        output_tokens=0,
        latency_ms=0,
        created_at=base,
    )

    wh_id = uuid.uuid4()
    wh_dto = WebhookEventDto(
        id=wh_id,
        event_type=WebhookEventType.NODE_STARTED,
        engine_run_id="engine-1",
        payload={"hello": "world"},
        signature_ok=True,
        source=WebhookSource.ENGINE,
        received_at=base,
        processed_at=None,
    )

    rs_id = uuid.uuid4()
    rs_dto = RunSignalDto(
        id=rs_id,
        run_id=run_id,
        name="implementation-complete",
        task_id="T-1",
        payload={"k": "v"},
        received_at=base,
        dedupe_key=f"{run_id}:implementation-complete:T-1",
    )

    # JSONL side: append one line per kind via the writer.
    await jsonl.record_step(run_id, step_dto)
    await jsonl.record_policy_call(run_id, pc_dto)
    await jsonl.record_webhook_event(run_id, wh_dto)
    await jsonl.record_operator_signal(run_id, rs_dto)

    # SQL side: insert matching rows with the same UUIDs + payloads.
    # The Postgres backend reads from these tables.
    async with session_factory() as session, session.begin():
        session.add(
            Step(
                id=step_id,
                run_id=run_id,
                step_number=1,
                node_name="node-A",
                node_inputs={"k": "v"},
                node_result={"ok": True},
                error=None,
                status=StepStatus.COMPLETED.value,
                dispatched_at=None,
                completed_at=base,
                created_at=base,
            )
        )
        session.add(
            PolicyCall(
                id=pc_id,
                run_id=run_id,
                step_id=step_id,
                prompt_context={},
                available_tools=[],
                provider="stub",
                model="stub-1",
                selected_tool="terminate",
                tool_arguments={},
                input_tokens=0,
                output_tokens=0,
                latency_ms=0,
                created_at=base,
            )
        )
        session.add(
            WebhookEvent(
                id=wh_id,
                run_id=run_id,
                event_type=WebhookEventType.NODE_STARTED.value,
                engine_run_id="engine-1",
                payload={"hello": "world"},
                signature_ok=True,
                source=WebhookSource.ENGINE.value,
                received_at=base,
                processed_at=None,
                dedupe_key=f"dk-{wh_id}",
            )
        )
        session.add(
            RunSignal(
                id=rs_id,
                run_id=run_id,
                name="implementation-complete",
                task_id="T-1",
                payload={"k": "v"},
                received_at=base,
                dedupe_key=f"{run_id}:implementation-complete:T-1",
            )
        )

    return {"base": base}


async def _collect(it: Any) -> list[Any]:
    return [r async for r in it]


@pytest.mark.asyncio
async def test_streams_are_byte_identical(
    tmp_path: Path,
    session_factory: async_sessionmaker[AsyncSession],
    parity_run: uuid.UUID,
) -> None:
    """Stage identical state, serialize both readers' output, diff."""
    jsonl = JsonlTraceStore(tmp_path)
    postgres = PostgresTraceStore(session_factory)

    await _stage_identical_state(
        run_id=parity_run,
        session_factory=session_factory,
        jsonl=jsonl,
    )

    jsonl_dtos = await _collect(jsonl.tail_run_stream(parity_run))
    postgres_dtos = await _collect(postgres.tail_run_stream(parity_run))

    jsonl_bytes = "".join(_serialize_trace_record(dto) for dto in jsonl_dtos)
    postgres_bytes = "".join(_serialize_trace_record(dto) for dto in postgres_dtos)

    if jsonl_bytes != postgres_bytes:
        # Diff on first differing line, with five lines of context, to
        # match the brief's debugging guidance.
        from difflib import unified_diff

        diff = "\n".join(
            unified_diff(
                jsonl_bytes.splitlines(),
                postgres_bytes.splitlines(),
                fromfile="jsonl",
                tofile="postgres",
                lineterm="",
                n=5,
            )
        )
        pytest.fail(f"NDJSON streams diverged:\n{diff}")


@pytest.mark.asyncio
async def test_filter_combo_is_byte_identical(
    tmp_path: Path,
    session_factory: async_sessionmaker[AsyncSession],
    parity_run: uuid.UUID,
) -> None:
    """The ``?kind=`` filter narrows both readers identically."""
    jsonl = JsonlTraceStore(tmp_path)
    postgres = PostgresTraceStore(session_factory)

    await _stage_identical_state(
        run_id=parity_run,
        session_factory=session_factory,
        jsonl=jsonl,
    )

    kinds = frozenset({"step", "operator_signal"})
    jsonl_dtos = await _collect(jsonl.tail_run_stream(parity_run, kinds=kinds))
    postgres_dtos = await _collect(postgres.tail_run_stream(parity_run, kinds=kinds))

    jsonl_bytes = "".join(_serialize_trace_record(dto) for dto in jsonl_dtos)
    postgres_bytes = "".join(_serialize_trace_record(dto) for dto in postgres_dtos)
    assert jsonl_bytes == postgres_bytes


@pytest.mark.asyncio
async def test_since_filter_is_byte_identical(
    tmp_path: Path,
    session_factory: async_sessionmaker[AsyncSession],
    parity_run: uuid.UUID,
) -> None:
    """The ``?since=`` filter excludes the same rows on both backends."""
    jsonl = JsonlTraceStore(tmp_path)
    postgres = PostgresTraceStore(session_factory)

    state = await _stage_identical_state(
        run_id=parity_run,
        session_factory=session_factory,
        jsonl=jsonl,
    )

    # Cutoff strictly after the staged ``base`` timestamp — both backends
    # should yield an empty stream.
    later = state["base"].replace(year=state["base"].year + 1)
    jsonl_dtos = await _collect(jsonl.tail_run_stream(parity_run, since=later))
    postgres_dtos = await _collect(postgres.tail_run_stream(parity_run, since=later))
    assert jsonl_dtos == []
    assert postgres_dtos == []
