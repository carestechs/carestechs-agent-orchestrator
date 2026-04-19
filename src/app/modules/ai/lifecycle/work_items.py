"""Work-item state-machine transitions (FEAT-006).

Implements the 6 explicit transitions (W1, W3, W4, W6) and the 2 derived
transitions (W2, W5) from the design doc.  Illegal transitions raise
:class:`ConflictError` with a descriptive ``detail``; the global handler
converts to ``409`` Problem Details.

Every transition function takes a ``SELECT ... FOR UPDATE`` on the
work-item row to serialize concurrent writes.  Derivations are idempotent
— calling them while already in the target state is a no-op.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.modules.ai.enums import TaskStatus, WorkItemStatus, WorkItemType
from app.modules.ai.models import Task, WorkItem

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _load_locked(db: AsyncSession, work_item_id: uuid.UUID) -> WorkItem:
    """Load a WorkItem row with ``SELECT ... FOR UPDATE`` applied."""
    row = await db.scalar(
        select(WorkItem).where(WorkItem.id == work_item_id).with_for_update()
    )
    if row is None:
        raise NotFoundError(f"work item not found: {work_item_id}")
    return row


def _forbidden(wi: WorkItem, target: WorkItemStatus) -> ConflictError:
    return ConflictError(
        f"work item {wi.id} cannot transition from {wi.status} to {target.value}"
    )


# ---------------------------------------------------------------------------
# W1 — open
# ---------------------------------------------------------------------------


async def open_work_item(
    db: AsyncSession,
    *,
    external_ref: str,
    type: WorkItemType,
    title: str,
    source_path: str | None,
    opened_by: str,
) -> WorkItem:
    """W1: create a new work item in the ``open`` state."""
    wi = WorkItem(
        external_ref=external_ref,
        type=type.value,
        title=title,
        source_path=source_path,
        status=WorkItemStatus.OPEN.value,
        opened_by=opened_by,
    )
    db.add(wi)
    await db.flush()
    await db.refresh(wi)
    return wi


# ---------------------------------------------------------------------------
# W3 — lock (admin pause)
# ---------------------------------------------------------------------------


async def lock_work_item(
    db: AsyncSession, work_item_id: uuid.UUID, *, actor: str
) -> WorkItem:
    """W3: ``in_progress -> locked`` (admin pause)."""
    wi = await _load_locked(db, work_item_id)
    if wi.status != WorkItemStatus.IN_PROGRESS.value:
        raise _forbidden(wi, WorkItemStatus.LOCKED)
    wi.locked_from = wi.status
    wi.status = WorkItemStatus.LOCKED.value
    await db.flush()
    await db.refresh(wi)
    return wi


# ---------------------------------------------------------------------------
# W4 — unlock (admin resume)
# ---------------------------------------------------------------------------


async def unlock_work_item(
    db: AsyncSession, work_item_id: uuid.UUID, *, actor: str
) -> WorkItem:
    """W4: ``locked -> in_progress`` (admin resume)."""
    wi = await _load_locked(db, work_item_id)
    if wi.status != WorkItemStatus.LOCKED.value:
        raise _forbidden(wi, WorkItemStatus.IN_PROGRESS)
    # locked_from recorded the prior state; we always return to in_progress
    # in v1 since lock is only reachable from there.
    wi.status = WorkItemStatus.IN_PROGRESS.value
    wi.locked_from = None
    await db.flush()
    await db.refresh(wi)
    return wi


# ---------------------------------------------------------------------------
# W6 — close (admin)
# ---------------------------------------------------------------------------


async def close_work_item(
    db: AsyncSession, work_item_id: uuid.UUID, *, actor: str
) -> WorkItem:
    """W6: ``ready -> closed`` (admin close)."""
    wi = await _load_locked(db, work_item_id)
    if wi.status != WorkItemStatus.READY.value:
        raise _forbidden(wi, WorkItemStatus.CLOSED)
    wi.status = WorkItemStatus.CLOSED.value
    wi.closed_at = datetime.now(UTC)
    wi.closed_by = actor
    await db.flush()
    await db.refresh(wi)
    return wi


# ---------------------------------------------------------------------------
# W2 — derived: first task approved -> in_progress
# ---------------------------------------------------------------------------


async def maybe_advance_to_in_progress(
    db: AsyncSession, work_item_id: uuid.UUID
) -> bool:
    """W2: advance ``open -> in_progress`` when the first task is approved.

    Idempotent.  Returns ``True`` if the state changed, ``False`` if the work
    item is already past ``open`` (including ``locked``, ``ready``, or
    ``closed``).
    """
    wi = await _load_locked(db, work_item_id)
    if wi.status != WorkItemStatus.OPEN.value:
        return False
    wi.status = WorkItemStatus.IN_PROGRESS.value
    await db.flush()
    await db.refresh(wi)
    return True


# ---------------------------------------------------------------------------
# W5 — derived: all tasks terminal -> ready
# ---------------------------------------------------------------------------


_TERMINAL_TASK_STATUSES = {TaskStatus.DONE.value, TaskStatus.DEFERRED.value}


async def maybe_advance_to_ready(
    db: AsyncSession, work_item_id: uuid.UUID
) -> bool:
    """W5: advance ``in_progress -> ready`` when every task is terminal.

    Idempotent and requires at least one child task; a zero-task work item
    does not advance (see FEAT-006 §9 edge cases).  Returns ``True`` on
    transition, ``False`` otherwise.
    """
    wi = await _load_locked(db, work_item_id)
    if wi.status != WorkItemStatus.IN_PROGRESS.value:
        return False

    total = await db.scalar(
        select(func.count()).select_from(Task).where(Task.work_item_id == work_item_id)
    )
    if not total:
        return False

    non_terminal = await db.scalar(
        select(func.count())
        .select_from(Task)
        .where(
            Task.work_item_id == work_item_id,
            Task.status.notin_(_TERMINAL_TASK_STATUSES),
        )
    )
    if non_terminal:
        return False

    wi.status = WorkItemStatus.READY.value
    await db.flush()
    await db.refresh(wi)
    return True
