"""Tests for the work-item state-machine service (FEAT-006 / T-112)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.modules.ai.enums import TaskStatus, WorkItemStatus, WorkItemType
from app.modules.ai.lifecycle import work_items as wi_svc
from app.modules.ai.models import Task, WorkItem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _open(db: AsyncSession, ref: str = "FEAT-001") -> WorkItem:
    wi = await wi_svc.open_work_item(
        db,
        external_ref=ref,
        type=WorkItemType.FEAT,
        title="Demo",
        source_path=None,
        opened_by="admin",
    )
    await db.commit()
    return wi


async def _seed_task(
    db: AsyncSession, work_item_id: uuid.UUID, *, external_ref: str, status: TaskStatus
) -> Task:
    task = Task(
        work_item_id=work_item_id,
        external_ref=external_ref,
        title="t",
        status=status.value,
        proposer_type="admin",
        proposer_id="admin",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# W1 — open
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_work_item(db_session: AsyncSession) -> None:
    wi = await _open(db_session)
    assert wi.status == WorkItemStatus.OPEN.value
    assert wi.external_ref == "FEAT-001"
    assert wi.opened_by == "admin"


# ---------------------------------------------------------------------------
# W2 — derived (first approval -> in_progress)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_w2_advances_from_open(db_session: AsyncSession) -> None:
    wi = await _open(db_session)
    changed = await wi_svc.maybe_advance_to_in_progress(db_session, wi.id)
    await db_session.commit()
    assert changed is True
    await db_session.refresh(wi)
    assert wi.status == WorkItemStatus.IN_PROGRESS.value


@pytest.mark.asyncio
async def test_w2_idempotent_second_call(db_session: AsyncSession) -> None:
    wi = await _open(db_session)
    await wi_svc.maybe_advance_to_in_progress(db_session, wi.id)
    await db_session.commit()
    changed = await wi_svc.maybe_advance_to_in_progress(db_session, wi.id)
    await db_session.commit()
    assert changed is False


# ---------------------------------------------------------------------------
# W3 / W4 — lock / unlock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lock_then_unlock(db_session: AsyncSession) -> None:
    wi = await _open(db_session)
    await wi_svc.maybe_advance_to_in_progress(db_session, wi.id)
    await db_session.commit()

    await wi_svc.lock_work_item(db_session, wi.id, actor="admin")
    await db_session.commit()
    await db_session.refresh(wi)
    assert wi.status == WorkItemStatus.LOCKED.value

    await wi_svc.unlock_work_item(db_session, wi.id, actor="admin")
    await db_session.commit()
    await db_session.refresh(wi)
    assert wi.status == WorkItemStatus.IN_PROGRESS.value


@pytest.mark.asyncio
async def test_lock_from_open_is_forbidden(db_session: AsyncSession) -> None:
    wi = await _open(db_session)
    with pytest.raises(ConflictError):
        await wi_svc.lock_work_item(db_session, wi.id, actor="admin")


@pytest.mark.asyncio
async def test_unlock_from_in_progress_is_forbidden(db_session: AsyncSession) -> None:
    wi = await _open(db_session)
    await wi_svc.maybe_advance_to_in_progress(db_session, wi.id)
    await db_session.commit()
    with pytest.raises(ConflictError):
        await wi_svc.unlock_work_item(db_session, wi.id, actor="admin")


# ---------------------------------------------------------------------------
# W5 — derived (all tasks terminal -> ready)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_w5_zero_tasks_does_not_advance(db_session: AsyncSession) -> None:
    wi = await _open(db_session)
    await wi_svc.maybe_advance_to_in_progress(db_session, wi.id)
    await db_session.commit()

    changed = await wi_svc.maybe_advance_to_ready(db_session, wi.id)
    await db_session.commit()
    assert changed is False


@pytest.mark.asyncio
async def test_w5_all_tasks_terminal(db_session: AsyncSession) -> None:
    wi = await _open(db_session)
    await wi_svc.maybe_advance_to_in_progress(db_session, wi.id)
    await db_session.commit()

    await _seed_task(db_session, wi.id, external_ref="T-1", status=TaskStatus.DONE)
    await _seed_task(db_session, wi.id, external_ref="T-2", status=TaskStatus.DEFERRED)

    changed = await wi_svc.maybe_advance_to_ready(db_session, wi.id)
    await db_session.commit()
    assert changed is True
    await db_session.refresh(wi)
    assert wi.status == WorkItemStatus.READY.value


@pytest.mark.asyncio
async def test_w5_non_terminal_blocks(db_session: AsyncSession) -> None:
    wi = await _open(db_session)
    await wi_svc.maybe_advance_to_in_progress(db_session, wi.id)
    await db_session.commit()

    await _seed_task(db_session, wi.id, external_ref="T-1", status=TaskStatus.DONE)
    await _seed_task(db_session, wi.id, external_ref="T-2", status=TaskStatus.IMPLEMENTING)

    changed = await wi_svc.maybe_advance_to_ready(db_session, wi.id)
    await db_session.commit()
    assert changed is False


@pytest.mark.asyncio
async def test_w5_idempotent_when_already_ready(db_session: AsyncSession) -> None:
    wi = await _open(db_session)
    await wi_svc.maybe_advance_to_in_progress(db_session, wi.id)
    await db_session.commit()
    await _seed_task(db_session, wi.id, external_ref="T-1", status=TaskStatus.DONE)
    await wi_svc.maybe_advance_to_ready(db_session, wi.id)
    await db_session.commit()

    changed = await wi_svc.maybe_advance_to_ready(db_session, wi.id)
    await db_session.commit()
    assert changed is False


# ---------------------------------------------------------------------------
# W6 — close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_only_from_ready(db_session: AsyncSession) -> None:
    wi = await _open(db_session)
    await wi_svc.maybe_advance_to_in_progress(db_session, wi.id)
    await db_session.commit()

    with pytest.raises(ConflictError):
        await wi_svc.close_work_item(db_session, wi.id, actor="admin")

    # advance to ready via W5
    await _seed_task(db_session, wi.id, external_ref="T-1", status=TaskStatus.DONE)
    await wi_svc.maybe_advance_to_ready(db_session, wi.id)
    await db_session.commit()

    await wi_svc.close_work_item(db_session, wi.id, actor="admin")
    await db_session.commit()
    await db_session.refresh(wi)
    assert wi.status == WorkItemStatus.CLOSED.value
    assert wi.closed_by == "admin"
    assert wi.closed_at is not None


# ---------------------------------------------------------------------------
# NotFound paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lock_unknown_id_raises_not_found(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await wi_svc.lock_work_item(db_session, uuid.uuid4(), actor="admin")
