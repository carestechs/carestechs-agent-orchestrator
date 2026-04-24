"""Work-item state-machine transitions (FEAT-006).

Implements the 6 explicit transitions (W1, W3, W4, W6) and the 2 derived
transitions (W2, W5) from the design doc.  Illegal transitions raise
:class:`ConflictError` with a descriptive ``detail``; the global handler
converts to ``409`` Problem Details.

Every transition function takes a ``SELECT ... FOR UPDATE`` on the
work-item row to serialize concurrent writes.  Derivations are idempotent
— calling them while already in the target state is a no-op.

**FEAT-006 rc2 / T-131a**: each transition now accepts an optional
:class:`FlowEngineLifecycleClient` + workflow id.  When both are present
(and the work-item row has an ``engine_item_id``), the transition
mirrors the state change onto the flow engine so other tools that
subscribe to the engine's webhooks see the same history.  Local state
remains authoritative — engine failures are logged and swallowed so a
transient engine outage never blocks the orchestrator.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.modules.ai.enums import TaskStatus, WorkItemStatus, WorkItemType
from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient
from app.modules.ai.models import Task, WorkItem

logger = logging.getLogger(__name__)

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


async def _mirror_to_engine(
    wi: WorkItem,
    to_status: WorkItemStatus,
    *,
    engine: FlowEngineLifecycleClient | None,
    correlation_id: uuid.UUID | None,
    actor: str | None,
) -> None:
    """Best-effort mirror write of a state change onto the flow engine.

    Swallows + logs engine errors — local state is authoritative in rc2
    phase 1.  A follow-up (rc3) will make the engine the sole writer.
    """
    if engine is None or wi.engine_item_id is None:
        return
    try:
        await engine.transition_item(
            item_id=wi.engine_item_id,
            to_status=to_status.value,
            correlation_id=correlation_id or uuid.uuid4(),
            actor=actor,
        )
    except Exception:
        logger.warning(
            "engine mirror write failed for work_item %s -> %s",
            wi.id,
            to_status.value,
            exc_info=True,
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
    engine: FlowEngineLifecycleClient | None = None,
    engine_workflow_id: uuid.UUID | None = None,
) -> WorkItem:
    """W1: create a new work item in the ``open`` state.

    When ``engine`` + ``engine_workflow_id`` are provided, a mirror item is
    created in the flow engine and its id is stored on the local row.
    """
    engine_item_id: uuid.UUID | None = None
    if engine is not None and engine_workflow_id is not None:
        try:
            engine_item_id = await engine.create_item(
                workflow_id=engine_workflow_id,
                title=title,
                external_ref=external_ref,
                metadata={"type": type.value, "source_path": source_path or ""},
            )
        except Exception:
            logger.warning(
                "engine create_item failed for work-item %s; continuing without mirror",
                external_ref,
                exc_info=True,
            )

    wi = WorkItem(
        external_ref=external_ref,
        type=type.value,
        title=title,
        source_path=source_path,
        status=WorkItemStatus.OPEN.value,
        opened_by=opened_by,
        engine_item_id=engine_item_id,
    )
    db.add(wi)
    await db.flush()
    await db.refresh(wi)
    return wi


# ---------------------------------------------------------------------------
# W3 — lock (admin pause)
# ---------------------------------------------------------------------------


async def lock_work_item(
    db: AsyncSession,
    work_item_id: uuid.UUID,
    *,
    actor: str,
    engine: FlowEngineLifecycleClient | None = None,
    correlation_id: uuid.UUID | None = None,
) -> WorkItem:
    """W3: ``in_progress -> locked`` (admin pause)."""
    wi = await _load_locked(db, work_item_id)
    if wi.status != WorkItemStatus.IN_PROGRESS.value:
        raise _forbidden(wi, WorkItemStatus.LOCKED)
    if engine is None:
        wi.status = WorkItemStatus.LOCKED.value
    await db.flush()
    await _mirror_to_engine(
        wi, WorkItemStatus.LOCKED, engine=engine, correlation_id=correlation_id, actor=actor
    )
    await db.refresh(wi)
    return wi


# ---------------------------------------------------------------------------
# W4 — unlock (admin resume)
# ---------------------------------------------------------------------------


async def unlock_work_item(
    db: AsyncSession,
    work_item_id: uuid.UUID,
    *,
    actor: str,
    engine: FlowEngineLifecycleClient | None = None,
    correlation_id: uuid.UUID | None = None,
) -> WorkItem:
    """W4: ``locked -> in_progress`` (admin resume)."""
    wi = await _load_locked(db, work_item_id)
    if wi.status != WorkItemStatus.LOCKED.value:
        raise _forbidden(wi, WorkItemStatus.IN_PROGRESS)
    # V1: lock is only reachable from in_progress, so unlock always
    # restores in_progress — no prior-state column needed (FEAT-008/T-168).
    if engine is None:
        wi.status = WorkItemStatus.IN_PROGRESS.value
    await db.flush()
    await _mirror_to_engine(
        wi,
        WorkItemStatus.IN_PROGRESS,
        engine=engine,
        correlation_id=correlation_id,
        actor=actor,
    )
    await db.refresh(wi)
    return wi


# ---------------------------------------------------------------------------
# W6 — close (admin)
# ---------------------------------------------------------------------------


async def close_work_item(
    db: AsyncSession,
    work_item_id: uuid.UUID,
    *,
    actor: str,
    engine: FlowEngineLifecycleClient | None = None,
    correlation_id: uuid.UUID | None = None,
) -> WorkItem:
    """W6: ``ready -> closed`` (admin close)."""
    wi = await _load_locked(db, work_item_id)
    if wi.status != WorkItemStatus.READY.value:
        raise _forbidden(wi, WorkItemStatus.CLOSED)
    if engine is None:
        wi.status = WorkItemStatus.CLOSED.value
    wi.closed_at = datetime.now(UTC)
    wi.closed_by = actor
    await db.flush()
    await _mirror_to_engine(
        wi,
        WorkItemStatus.CLOSED,
        engine=engine,
        correlation_id=correlation_id,
        actor=actor,
    )
    await db.refresh(wi)
    return wi


# ---------------------------------------------------------------------------
# W2 — derived: first task approved -> in_progress
# ---------------------------------------------------------------------------


async def maybe_advance_to_in_progress(
    db: AsyncSession,
    work_item_id: uuid.UUID,
    *,
    engine: FlowEngineLifecycleClient | None = None,
    correlation_id: uuid.UUID | None = None,
) -> bool:
    """W2: advance ``open -> in_progress`` when the first task is approved.

    Idempotent.  Returns ``True`` if the state changed, ``False`` if the work
    item is already past ``open`` (including ``locked``, ``ready``, or
    ``closed``).
    """
    wi = await _load_locked(db, work_item_id)
    if wi.status != WorkItemStatus.OPEN.value:
        return False
    # FIXME(FEAT-008/T-169): derivation still writes status inline under
    # both engine-present and engine-absent. Moving it to the reactor
    # means issuing the engine transition from reactor-side and waiting
    # for the return webhook — a bigger refactor deferred to a follow-on.
    wi.status = WorkItemStatus.IN_PROGRESS.value
    await db.flush()
    await _mirror_to_engine(
        wi,
        WorkItemStatus.IN_PROGRESS,
        engine=engine,
        correlation_id=correlation_id,
        actor="system:W2",
    )
    await db.refresh(wi)
    return True


# ---------------------------------------------------------------------------
# W5 — derived: all tasks terminal -> ready
# ---------------------------------------------------------------------------


_TERMINAL_TASK_STATUSES = {TaskStatus.DONE.value, TaskStatus.DEFERRED.value}


async def maybe_advance_to_ready(
    db: AsyncSession,
    work_item_id: uuid.UUID,
    *,
    engine: FlowEngineLifecycleClient | None = None,
    correlation_id: uuid.UUID | None = None,
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

    # FIXME(FEAT-008/T-169): derivation stays inline — see maybe_advance_to_in_progress.
    wi.status = WorkItemStatus.READY.value
    await db.flush()
    await _mirror_to_engine(
        wi,
        WorkItemStatus.READY,
        engine=engine,
        correlation_id=correlation_id,
        actor="system:W5",
    )
    await db.refresh(wi)
    return True
