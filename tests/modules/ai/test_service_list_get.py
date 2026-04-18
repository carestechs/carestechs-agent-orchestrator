"""Service-layer tests for ``list_runs`` + ``get_run`` (T-041)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.modules.ai.enums import RunStatus, StepStatus
from app.modules.ai.models import Run, RunMemory, Step
from app.modules.ai.service import get_run, list_runs


async def _seed_run(
    db: AsyncSession,
    *,
    agent_ref: str,
    status: RunStatus,
    started_at: datetime,
) -> Run:
    run = Run(
        agent_ref=agent_ref,
        agent_definition_hash="sha256:" + "0" * 64,
        intake={},
        status=status,
        started_at=started_at,
        trace_uri="file:///tmp/t.jsonl",
    )
    db.add(run)
    await db.flush()
    db.add(RunMemory(run_id=run.id, data={}))
    return run


async def _seed_step(db: AsyncSession, run_id: uuid.UUID, step_number: int) -> Step:
    step = Step(
        run_id=run_id,
        step_number=step_number,
        node_name=f"node-{step_number}",
        node_inputs={},
        status=StepStatus.PENDING,
    )
    db.add(step)
    await db.flush()
    return step


# ---------------------------------------------------------------------------
# list_runs
# ---------------------------------------------------------------------------


class TestListRuns:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_filters_returns_all_sorted_by_started_at_desc(
        self, db_session: AsyncSession
    ) -> None:
        now = datetime.now(UTC)
        a = await _seed_run(
            db_session, agent_ref="agent-a", status=RunStatus.PENDING, started_at=now
        )
        b = await _seed_run(
            db_session,
            agent_ref="agent-b",
            status=RunStatus.RUNNING,
            started_at=now + timedelta(seconds=1),
        )
        c = await _seed_run(
            db_session,
            agent_ref="agent-a",
            status=RunStatus.COMPLETED,
            started_at=now - timedelta(seconds=1),
        )
        await db_session.commit()

        items, total = await list_runs(db_session)
        ids = [i.id for i in items]

        assert total == 3
        assert ids == [b.id, a.id, c.id]  # newest first

    @pytest.mark.asyncio(loop_scope="function")
    async def test_status_filter(self, db_session: AsyncSession) -> None:
        now = datetime.now(UTC)
        await _seed_run(
            db_session, agent_ref="a", status=RunStatus.PENDING, started_at=now
        )
        await _seed_run(
            db_session,
            agent_ref="b",
            status=RunStatus.COMPLETED,
            started_at=now + timedelta(seconds=1),
        )
        await db_session.commit()

        items, total = await list_runs(db_session, status="pending")
        assert total == 1
        assert items[0].status == RunStatus.PENDING

    @pytest.mark.asyncio(loop_scope="function")
    async def test_agent_ref_filter(self, db_session: AsyncSession) -> None:
        now = datetime.now(UTC)
        await _seed_run(
            db_session, agent_ref="agent-a", status=RunStatus.PENDING, started_at=now
        )
        await _seed_run(
            db_session,
            agent_ref="agent-b",
            status=RunStatus.PENDING,
            started_at=now + timedelta(seconds=1),
        )
        await db_session.commit()

        items, total = await list_runs(db_session, agent_ref="agent-b")
        assert total == 1
        assert items[0].agent_ref == "agent-b"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_combined_filters_are_anded(self, db_session: AsyncSession) -> None:
        now = datetime.now(UTC)
        await _seed_run(
            db_session, agent_ref="a", status=RunStatus.PENDING, started_at=now
        )
        await _seed_run(
            db_session, agent_ref="a", status=RunStatus.COMPLETED, started_at=now
        )
        await _seed_run(
            db_session, agent_ref="b", status=RunStatus.PENDING, started_at=now
        )
        await db_session.commit()

        items, total = await list_runs(
            db_session, status="pending", agent_ref="a"
        )
        assert total == 1
        assert items[0].agent_ref == "a"
        assert items[0].status == RunStatus.PENDING

    @pytest.mark.asyncio(loop_scope="function")
    async def test_pagination_yields_each_run_exactly_once(
        self, db_session: AsyncSession
    ) -> None:
        now = datetime.now(UTC)
        for i in range(3):
            await _seed_run(
                db_session,
                agent_ref="p",
                status=RunStatus.PENDING,
                started_at=now + timedelta(seconds=i),
            )
        await db_session.commit()

        seen: set[uuid.UUID] = set()
        for page in (1, 2, 3):
            items, total = await list_runs(
                db_session, agent_ref="p", page=page, page_size=1
            )
            assert total == 3
            assert len(items) == 1
            seen.add(items[0].id)

        assert len(seen) == 3

    @pytest.mark.asyncio(loop_scope="function")
    async def test_page_beyond_total_returns_empty_but_correct_total(
        self, db_session: AsyncSession
    ) -> None:
        now = datetime.now(UTC)
        await _seed_run(
            db_session, agent_ref="x", status=RunStatus.PENDING, started_at=now
        )
        await db_session.commit()

        items, total = await list_runs(db_session, page=5, page_size=10)
        assert items == []
        assert total == 1


# ---------------------------------------------------------------------------
# get_run
# ---------------------------------------------------------------------------


class TestGetRun:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_unknown_id_raises_not_found(self, db_session: AsyncSession) -> None:
        with pytest.raises(NotFoundError):
            await get_run(uuid.uuid4(), db_session)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_zero_steps_yields_none_last_step(
        self, db_session: AsyncSession
    ) -> None:
        run = await _seed_run(
            db_session,
            agent_ref="a",
            status=RunStatus.PENDING,
            started_at=datetime.now(UTC),
        )
        await db_session.commit()

        detail = await get_run(run.id, db_session)
        assert detail.step_count == 0
        assert detail.last_step is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_last_step_populated_with_highest_step_number(
        self, db_session: AsyncSession
    ) -> None:
        run = await _seed_run(
            db_session,
            agent_ref="a",
            status=RunStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        await _seed_step(db_session, run.id, 1)
        await _seed_step(db_session, run.id, 2)
        third = await _seed_step(db_session, run.id, 3)
        await db_session.commit()

        detail = await get_run(run.id, db_session)
        assert detail.step_count == 3
        assert detail.last_step is not None
        assert detail.last_step.step_number == 3
        assert detail.last_step.id == third.id
