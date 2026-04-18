"""Tests for the RunSignal repository helpers (FEAT-005 / T-088)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import RunStatus
from app.modules.ai.models import Run, RunSignal
from app.modules.ai.repository import (
    compute_signal_dedupe_key,
    create_run_signal,
    select_signals_for_run,
)


async def _seed_run(db: AsyncSession) -> Run:
    run = Run(
        agent_ref="lifecycle-agent@0.1.0",
        agent_definition_hash="sha256:" + "0" * 64,
        intake={},
        status=RunStatus.RUNNING,
        started_at=datetime.now(UTC),
        trace_uri="file:///tmp/t.jsonl",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


@pytest.mark.asyncio
class TestCreateRunSignal:
    async def test_happy_insert(self, db_session: AsyncSession) -> None:
        run = await _seed_run(db_session)
        key = compute_signal_dedupe_key(run.id, "implementation-complete", "T-001")

        row, created = await create_run_signal(
            db_session,
            run_id=run.id,
            name="implementation-complete",
            task_id="T-001",
            payload={"commit_sha": "abc123"},
            dedupe_key=key,
        )
        await db_session.commit()

        assert created is True
        assert row.name == "implementation-complete"
        assert row.task_id == "T-001"
        assert row.payload == {"commit_sha": "abc123"}
        assert row.dedupe_key == key

    async def test_duplicate_returns_existing(self, db_session: AsyncSession) -> None:
        run = await _seed_run(db_session)
        key = compute_signal_dedupe_key(run.id, "implementation-complete", "T-002")

        first, created_first = await create_run_signal(
            db_session,
            run_id=run.id,
            name="implementation-complete",
            task_id="T-002",
            payload={},
            dedupe_key=key,
        )
        await db_session.commit()

        second, created_second = await create_run_signal(
            db_session,
            run_id=run.id,
            name="implementation-complete",
            task_id="T-002",
            payload={"ignored": True},
            dedupe_key=key,
        )
        await db_session.commit()

        assert created_first is True
        assert created_second is False
        assert first.id == second.id
        # Second call's payload is ignored (on-conflict-do-nothing).
        assert second.payload == {}

    async def test_unknown_run_id_raises(self, db_session: AsyncSession) -> None:
        ghost = uuid.uuid4()
        key = compute_signal_dedupe_key(ghost, "implementation-complete", "T-001")
        with pytest.raises(IntegrityError):
            await create_run_signal(
                db_session,
                run_id=ghost,
                name="implementation-complete",
                task_id="T-001",
                payload={},
                dedupe_key=key,
            )


@pytest.mark.asyncio
class TestSelectSignalsForRun:
    async def test_returns_rows_in_received_order(
        self, db_session: AsyncSession
    ) -> None:
        run = await _seed_run(db_session)

        for task_id in ("T-001", "T-002", "T-003"):
            key = compute_signal_dedupe_key(run.id, "implementation-complete", task_id)
            await create_run_signal(
                db_session,
                run_id=run.id,
                name="implementation-complete",
                task_id=task_id,
                payload={},
                dedupe_key=key,
            )
            await db_session.commit()

        rows = await select_signals_for_run(db_session, run.id)
        assert [r.task_id for r in rows] == ["T-001", "T-002", "T-003"]
        assert all(isinstance(r, RunSignal) for r in rows)


class TestDedupeKeyHelper:
    def test_deterministic(self) -> None:
        run_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        a = compute_signal_dedupe_key(run_id, "implementation-complete", "T-001")
        b = compute_signal_dedupe_key(run_id, "implementation-complete", "T-001")
        assert a == b

    def test_differs_by_task_id(self) -> None:
        run_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        a = compute_signal_dedupe_key(run_id, "implementation-complete", "T-001")
        b = compute_signal_dedupe_key(run_id, "implementation-complete", "T-002")
        assert a != b

    def test_null_task_id(self) -> None:
        run_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        key = compute_signal_dedupe_key(run_id, "some-signal", None)
        assert key  # non-empty
