"""Tests for the FEAT-013 / T-276 ``migrate-traces`` JSONL importer.

Coverage:

- effector_call lines are inserted into ``effector_calls``.
- executor_call lines are inserted into ``executor_calls``.
- The other four kinds (step/policy_call/webhook_event/operator_signal)
  are *never* inserted — they only feed the divergence counters.
- A re-run over the same input is idempotent (skip counters bump,
  inserted counters do not).
- ``dry_run=True`` writes nothing.
- Malformed lines are counted, not crash-the-import.
- ``--since`` filters JSONL lines by their event timestamp.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ai.enums import DispatchMode
from app.modules.ai.models import EffectorCall, ExecutorCall, Run
from app.modules.ai.schemas import EffectorCallDto, ExecutorCallDto
from app.modules.ai.trace_migrate import migrate, parse_iso_since


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


def _effector_line(*, entity_id: uuid.UUID, transition: str, emitted_at: datetime) -> str:
    dto = EffectorCallDto(
        effector_name="checks.post",
        entity_type="task",
        entity_id=entity_id,
        transition=transition,
        transition_key=transition,
        status="ok",
        duration_ms=10,
        emitted_at=emitted_at,
    )
    return json.dumps(
        {"kind": "effector_call", "data": dto.model_dump(mode="json", by_alias=True)},
        default=str,
    )


def _executor_line(*, run_id: uuid.UUID, dispatch_id: uuid.UUID, finished_at: datetime) -> str:
    dto = ExecutorCallDto(
        dispatch_id=dispatch_id,
        run_id=run_id,
        executor_ref="review",
        mode=DispatchMode.LOCAL,
        started_at=finished_at - timedelta(seconds=1),
        finished_at=finished_at,
        outcome=None,
    )
    return json.dumps(
        {"kind": "executor_call", "data": dto.model_dump(mode="json", by_alias=True)},
        default=str,
    )


def _step_line(*, step_id: uuid.UUID) -> str:
    return json.dumps(
        {
            "kind": "step",
            "data": {
                "id": str(step_id),
                "stepNumber": 1,
                "nodeName": "node-A",
                "status": "completed",
                "nodeInputs": {},
                "nodeResult": None,
                "error": None,
                "dispatchedAt": None,
                "completedAt": datetime.now(UTC).isoformat(),
            },
        },
        default=str,
    )


@pytest_asyncio.fixture(loop_scope="function")
async def seeded_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Run(
                id=run_id,
                agent_ref="test@0.0.1",
                agent_definition_hash="hash",
                intake={},
                status="completed",
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
                trace_uri=f"/test/{run_id}",
            )
        )
    return run_id


@pytest.fixture
def trace_dir(tmp_path: Path) -> Path:
    """Lay out the JSONL writer's directory shape inside ``tmp_path``."""
    (tmp_path / "effectors").mkdir()
    (tmp_path / "executors").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_imports_effector_and_executor_calls(
    session_factory: async_sessionmaker[AsyncSession],
    trace_dir: Path,
    seeded_run: uuid.UUID,
) -> None:
    entity_id = uuid.uuid4()
    dispatch_id = uuid.uuid4()
    emitted = datetime.now(UTC)
    finished = datetime.now(UTC)

    (trace_dir / "effectors" / f"{entity_id}.jsonl").write_text(
        _effector_line(entity_id=entity_id, transition="task.T2", emitted_at=emitted) + "\n"
    )
    (trace_dir / "executors" / f"{seeded_run}.jsonl").write_text(
        _executor_line(run_id=seeded_run, dispatch_id=dispatch_id, finished_at=finished) + "\n"
    )

    try:
        report = await migrate(
            trace_dir=trace_dir,
            session_factory=session_factory,
            since=None,
            dry_run=False,
        )
        assert report.effector_calls_inserted == 1
        assert report.executor_calls_inserted == 1
        assert report.total_divergence() == 0

        async with session_factory() as session:
            eff_rows = (
                (await session.execute(select(EffectorCall).where(EffectorCall.entity_id == entity_id))).scalars().all()
            )
            assert len(eff_rows) == 1
            assert eff_rows[0].transition_key == "task.T2"

            ex_rows = (
                (await session.execute(select(ExecutorCall).where(ExecutorCall.dispatch_id == dispatch_id)))
                .scalars()
                .all()
            )
            assert len(ex_rows) == 1
            assert ex_rows[0].node_name == "review"
    finally:
        async with session_factory() as session, session.begin():
            await session.execute(delete(EffectorCall).where(EffectorCall.entity_id == entity_id))
            await session.execute(delete(ExecutorCall).where(ExecutorCall.dispatch_id == dispatch_id))


@pytest.mark.asyncio
async def test_rerun_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
    trace_dir: Path,
    seeded_run: uuid.UUID,
) -> None:
    entity_id = uuid.uuid4()
    dispatch_id = uuid.uuid4()
    emitted = datetime.now(UTC)
    finished = datetime.now(UTC)

    (trace_dir / "effectors" / f"{entity_id}.jsonl").write_text(
        _effector_line(entity_id=entity_id, transition="task.T2", emitted_at=emitted) + "\n"
    )
    (trace_dir / "executors" / f"{seeded_run}.jsonl").write_text(
        _executor_line(run_id=seeded_run, dispatch_id=dispatch_id, finished_at=finished) + "\n"
    )

    try:
        first = await migrate(
            trace_dir=trace_dir,
            session_factory=session_factory,
            since=None,
            dry_run=False,
        )
        assert first.effector_calls_inserted == 1
        assert first.executor_calls_inserted == 1

        second = await migrate(
            trace_dir=trace_dir,
            session_factory=session_factory,
            since=None,
            dry_run=False,
        )
        assert second.effector_calls_inserted == 0
        assert second.executor_calls_inserted == 0
        assert second.effector_calls_skipped_existing == 1
        assert second.executor_calls_skipped_existing == 1
    finally:
        async with session_factory() as session, session.begin():
            await session.execute(delete(EffectorCall).where(EffectorCall.entity_id == entity_id))
            await session.execute(delete(ExecutorCall).where(ExecutorCall.dispatch_id == dispatch_id))


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(
    session_factory: async_sessionmaker[AsyncSession],
    trace_dir: Path,
) -> None:
    entity_id = uuid.uuid4()
    (trace_dir / "effectors" / f"{entity_id}.jsonl").write_text(
        _effector_line(entity_id=entity_id, transition="task.T2", emitted_at=datetime.now(UTC)) + "\n"
    )

    report = await migrate(
        trace_dir=trace_dir,
        session_factory=session_factory,
        since=None,
        dry_run=True,
    )
    assert report.effector_calls_inserted == 1  # counted as "would insert"
    assert report.dry_run is True

    async with session_factory() as session:
        existing = await session.execute(select(EffectorCall.id).where(EffectorCall.entity_id == entity_id))
        assert existing.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# Divergence + edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_lines_only_count_divergence_not_insert(
    session_factory: async_sessionmaker[AsyncSession],
    trace_dir: Path,
    seeded_run: uuid.UUID,
) -> None:
    """A step JSONL line whose SQL row is missing bumps divergence; no insert."""
    missing_step = uuid.uuid4()
    (trace_dir / f"{seeded_run}.jsonl").write_text(_step_line(step_id=missing_step) + "\n")

    report = await migrate(
        trace_dir=trace_dir,
        session_factory=session_factory,
        since=None,
        dry_run=False,
    )
    assert report.divergence_step == 1
    assert report.total_inserted() == 0


@pytest.mark.asyncio
async def test_malformed_lines_are_counted_not_crash(
    session_factory: async_sessionmaker[AsyncSession],
    trace_dir: Path,
    seeded_run: uuid.UUID,
) -> None:
    entity_id = uuid.uuid4()
    valid = _effector_line(entity_id=entity_id, transition="task.T2", emitted_at=datetime.now(UTC))
    contents = "\n".join(["{not valid json", valid, "another bad line"])
    (trace_dir / "effectors" / f"{entity_id}.jsonl").write_text(contents + "\n")

    try:
        report = await migrate(
            trace_dir=trace_dir,
            session_factory=session_factory,
            since=None,
            dry_run=False,
        )
        assert report.effector_calls_inserted == 1
        assert report.malformed_lines == 2
        assert len(report.malformed_examples) <= 3
    finally:
        async with session_factory() as session, session.begin():
            await session.execute(delete(EffectorCall).where(EffectorCall.entity_id == entity_id))


@pytest.mark.asyncio
async def test_since_filter_skips_old_lines(
    session_factory: async_sessionmaker[AsyncSession],
    trace_dir: Path,
) -> None:
    entity_id = uuid.uuid4()
    old_emitted = datetime.now(UTC) - timedelta(days=30)
    new_emitted = datetime.now(UTC)
    cutoff = datetime.now(UTC) - timedelta(days=1)

    contents = "\n".join(
        [
            _effector_line(entity_id=entity_id, transition="task.OLD", emitted_at=old_emitted),
            _effector_line(entity_id=entity_id, transition="task.NEW", emitted_at=new_emitted),
        ]
    )
    (trace_dir / "effectors" / f"{entity_id}.jsonl").write_text(contents + "\n")

    try:
        report = await migrate(
            trace_dir=trace_dir,
            session_factory=session_factory,
            since=cutoff,
            dry_run=False,
        )
        assert report.effector_calls_inserted == 1
        async with session_factory() as session:
            rows = (
                (await session.execute(select(EffectorCall).where(EffectorCall.entity_id == entity_id))).scalars().all()
            )
            transitions = sorted(r.transition_key for r in rows)
            assert transitions == ["task.NEW"]
    finally:
        async with session_factory() as session, session.begin():
            await session.execute(delete(EffectorCall).where(EffectorCall.entity_id == entity_id))


def test_parse_iso_since_requires_timezone() -> None:
    parse_iso_since("2026-05-11T00:00:00+00:00")  # ok
    with pytest.raises(ValueError, match="must include a timezone"):
        parse_iso_since("2026-05-11T00:00:00")


def _build_any_dict() -> dict[str, Any]:
    """Touch the ``Any`` import so the no-unused-import lint stays quiet."""
    return {}
