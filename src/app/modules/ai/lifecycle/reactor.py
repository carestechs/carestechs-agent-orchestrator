"""Engine webhook reactor (FEAT-006 rc2 / T-130).

When the flow engine emits a state-change event (``item.transitioned``),
the orchestrator receives it at
``/hooks/engine/lifecycle/item-transitioned`` and dispatches to
:func:`handle_transition`, which:

1. Maps the engine ``itemId`` to a local ``WorkItem`` or ``Task`` row via
   ``engine_item_id`` (both tables have it, unique).
2. Fires the appropriate derivation:
   - ``task_workflow`` + ``toStatus in {done, deferred}`` → call
     :func:`work_items.maybe_advance_to_ready` on the parent.
   - ``task_workflow`` + ``toStatus == approved`` → call
     :func:`work_items.maybe_advance_to_in_progress` on the parent (W2).
3. Is idempotent — replayed deliveries via ``WebhookEvent.dedupe_key``
   UNIQUE short-circuit before any side effect.

**Scope note (phase 1)**: the reactor only fires derivations.  Auxiliary
writes (``Approval``, ``TaskAssignment``, ``TaskPlan``,
``TaskImplementation``) still happen inline in the signal handlers —
that changes when correlation-context plumbing (T-133) lands.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import TaskStatus, WorkItemStatus
from app.modules.ai.lifecycle import declarations, work_items
from app.modules.ai.lifecycle.engine_client import extract_correlation_id
from app.modules.ai.models import PendingSignalContext, Task, WorkItem

logger = logging.getLogger(__name__)


class LifecycleWebhookData(BaseModel):
    """``data`` payload within the engine's ``item.transitioned`` webhook."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel, extra="ignore")

    from_status: str | None = None
    to_status: str | None = None
    triggered_by: str | None = None


class LifecycleWebhookEvent(BaseModel):
    """Full shape of the engine's item-lifecycle webhook body."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel, extra="ignore")

    delivery_id: uuid.UUID
    event_type: str
    tenant_id: uuid.UUID
    workflow_id: uuid.UUID
    item_id: uuid.UUID
    timestamp: datetime
    data: LifecycleWebhookData


_TASK_TERMINAL = {TaskStatus.DONE.value, TaskStatus.DEFERRED.value}


async def handle_transition(
    db: AsyncSession,
    event: LifecycleWebhookEvent,
    *,
    workflow_name_by_id: dict[uuid.UUID, str] | None = None,
) -> None:
    """Dispatch a lifecycle webhook to the appropriate derivation.

    *workflow_name_by_id* is a mapping the reactor uses to resolve which
    of the two workflows the item belongs to.  Supplied by the caller
    (the route handler) from ``app.state.lifecycle_workflow_ids``.
    """
    workflow_name = None
    if workflow_name_by_id is not None:
        workflow_name = workflow_name_by_id.get(event.workflow_id)

    if workflow_name is None:
        # Fall back to heuristic: look up task first, then work item.
        workflow_name = await _infer_workflow_from_item(db, event.item_id)
        if workflow_name is None:
            logger.info(
                "lifecycle webhook for unknown workflow %s; item %s not found locally",
                event.workflow_id,
                event.item_id,
            )
            return

    # Consume any correlation context recorded at signal time.  Observability
    # only for now — the row is logged + deleted.  A future phase will use
    # the payload to write auxiliary rows (Approval, TaskAssignment, etc.)
    # reactively rather than inline in the signal adapter.
    await _consume_correlation(db, event.data.triggered_by)

    to_status = event.data.to_status
    if workflow_name == declarations.TASK_WORKFLOW_NAME:
        await _handle_task_transition(db, event.item_id, to_status)
    elif workflow_name == declarations.WORK_ITEM_WORKFLOW_NAME:
        # Work-item transitions trigger no derivations directly — the
        # state machine already captured them at the orchestrator side.
        logger.debug(
            "work-item lifecycle webhook for item %s (%s); no derivation",
            event.item_id,
            to_status,
        )
    else:
        logger.debug("lifecycle webhook for unrecognised workflow %s", workflow_name)


async def _consume_correlation(
    db: AsyncSession, triggered_by: str | None
) -> None:
    """Parse the correlation UUID out of ``triggered_by``, look up the
    matching ``PendingSignalContext`` row, log it, and delete.

    No-ops when the correlation is absent or the row was already consumed
    (replayed webhook).  Aux-row writes based on this payload are
    intentionally deferred — this step only proves the end-to-end loop
    closes.
    """
    corr = extract_correlation_id(triggered_by)
    if corr is None:
        return
    row = await db.scalar(
        select(PendingSignalContext).where(
            PendingSignalContext.correlation_id == corr
        )
    )
    if row is None:
        logger.debug(
            "no pending_signal_context for correlation %s (already consumed?)",
            corr,
        )
        return
    logger.info(
        "reactor consumed correlation %s (signal=%s payload=%s)",
        corr,
        row.signal_name,
        row.payload,
    )
    await db.delete(row)
    await db.flush()


async def _infer_workflow_from_item(
    db: AsyncSession, engine_item_id: uuid.UUID
) -> str | None:
    task = await db.scalar(select(Task).where(Task.engine_item_id == engine_item_id))
    if task is not None:
        return declarations.TASK_WORKFLOW_NAME
    wi = await db.scalar(
        select(WorkItem).where(WorkItem.engine_item_id == engine_item_id)
    )
    if wi is not None:
        return declarations.WORK_ITEM_WORKFLOW_NAME
    return None


async def _handle_task_transition(
    db: AsyncSession,
    engine_item_id: uuid.UUID,
    to_status: str | None,
) -> None:
    task = await db.scalar(select(Task).where(Task.engine_item_id == engine_item_id))
    if task is None:
        logger.info(
            "task lifecycle webhook for unknown engine_item_id %s; skipping",
            engine_item_id,
        )
        return

    if to_status == TaskStatus.APPROVED.value:
        # W2 — first task approved flips work-item open -> in_progress.
        await work_items.maybe_advance_to_in_progress(db, task.work_item_id)
        return

    if to_status in _TASK_TERMINAL:
        # W5 — all tasks terminal flips work-item in_progress -> ready.
        await work_items.maybe_advance_to_ready(db, task.work_item_id)
        return

    # Other transitions (planning, plan_review, etc.) don't fire derivations
    # in rc2 phase 1.  Log at debug for forensics.
    logger.debug(
        "task %s transitioned to %s; no derivation", task.id, to_status
    )
_ = WorkItemStatus  # keep import alive for future extensions (impl-review etc.)
