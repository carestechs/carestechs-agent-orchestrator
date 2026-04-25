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
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.modules.ai.enums import TaskStatus, WorkItemStatus
from app.modules.ai.lifecycle import declarations, work_items
from app.modules.ai.lifecycle.effectors.context import EffectorContext
from app.modules.ai.lifecycle.effectors.registry import (
    EffectorRegistry,
    build_transition_key,
)
from app.modules.ai.lifecycle.engine_client import extract_correlation_id
from app.modules.ai.models import (
    Approval,
    PendingAuxWrite,
    PendingSignalContext,
    Task,
    TaskAssignment,
    TaskImplementation,
    TaskPlan,
    WorkItem,
)

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
    registry: EffectorRegistry | None = None,
    settings: Settings | None = None,
) -> None:
    """Dispatch a lifecycle webhook to the appropriate derivation.

    *workflow_name_by_id* is a mapping the reactor uses to resolve which
    of the two workflows the item belongs to.  Supplied by the caller
    (the route handler) from ``app.state.lifecycle_workflow_ids``.

    *registry* + *settings* are supplied by the route handler from
    ``app.state.effector_registry`` + the cached settings (FEAT-008/T-173).
    When either is ``None`` the reactor skips effector dispatch — keeps
    test fixtures that don't care about effectors free of registry
    boilerplate.
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

    # FEAT-008/T-167: materialize any outbox-queued aux row for this
    # correlation before firing derivations — downstream work that reads
    # aux rows (W5 via child-task counts, effectors that inspect latest
    # Approval/TaskImplementation) expects them already committed.
    corr = extract_correlation_id(event.data.triggered_by)
    if corr is not None:
        await _materialize_aux(db, corr)

    # FEAT-008/T-169: write the local status cache from the engine's
    # authoritative state. Fired for every webhook (correlation or not)
    # so engine-initiated transitions outside an orchestrator signal
    # still converge.
    await _update_status_cache(db, workflow_name, event)

    # Consume the signal-context row recorded at signal time.  Logged +
    # deleted; payload is used by the outbox materialization above when
    # the correlation matched.
    await _consume_correlation(db, event.data.triggered_by)

    # FEAT-008/T-173: dispatch registered effectors for this transition.
    # Fires after the status-cache write so effectors observing the local
    # row see the engine's authoritative state. Fires before W2/W5
    # derivations so the originating transition's effectors run first.
    if registry is not None and settings is not None:
        await _dispatch_effectors(
            db, event, workflow_name, registry, settings, corr
        )

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


async def _update_status_cache(
    db: AsyncSession,
    workflow_name: str,
    event: LifecycleWebhookEvent,
) -> None:
    """Write the engine's ``to_status`` onto the local cache row (T-169).

    Lookup by ``engine_item_id`` — both ``tasks`` and ``work_items``
    carry it (FEAT-006 rc2). Cache miss is logged and skipped; it can
    happen if the engine emits events for items the orchestrator never
    created (should not happen under the architecture, but cheap to
    guard).
    """
    to_status = event.data.to_status
    if to_status is None:
        return
    if workflow_name == declarations.TASK_WORKFLOW_NAME:
        task = await db.scalar(
            select(Task).where(Task.engine_item_id == event.item_id)
        )
        if task is None:
            logger.info(
                "status cache miss for task engine_item_id=%s; skipping",
                event.item_id,
            )
            return
        task.status = to_status
    elif workflow_name == declarations.WORK_ITEM_WORKFLOW_NAME:
        wi = await db.scalar(
            select(WorkItem).where(WorkItem.engine_item_id == event.item_id)
        )
        if wi is None:
            logger.info(
                "status cache miss for work_item engine_item_id=%s; skipping",
                event.item_id,
            )
            return
        wi.status = to_status
    else:
        return
    await db.flush()


async def _materialize_aux(
    db: AsyncSession, correlation_id: uuid.UUID
) -> None:
    """Materialize an outbox-queued aux row (FEAT-008/T-167).

    The signal adapter enqueued a ``PendingAuxWrite`` inside the same
    transaction that committed the idempotency key + engine mirror. On
    the engine's ``item.transitioned`` webhook arrival, this function
    locks that row (``FOR UPDATE SKIP LOCKED``), builds the target aux
    row from ``payload['aux_type']``, inserts it, deletes the outbox
    row, and flushes so the rest of the reactor sees the write.

    Idempotent on duplicate webhook delivery: the second arrival finds
    no row (already deleted) and no-ops.

    Replayed/unknown correlation ids (engine replay after outbox purge,
    or a correlation the orchestrator never enqueued) also no-op — they
    are logged at debug for forensics.
    """
    pending = await db.scalar(
        select(PendingAuxWrite)
        .where(PendingAuxWrite.correlation_id == correlation_id)
        .with_for_update(skip_locked=True)
    )
    if pending is None:
        logger.debug(
            "no pending_aux_write for correlation %s (already materialized?)",
            correlation_id,
        )
        return
    aux_row = build_aux_row(pending)
    if aux_row is not None:
        db.add(aux_row)
    else:
        logger.warning(
            "pending_aux_write %s has unknown aux_type=%r; dropping",
            correlation_id,
            pending.payload.get("aux_type"),
        )
    await db.delete(pending)
    await db.flush()


def build_aux_row(
    pending: PendingAuxWrite,
) -> Approval | TaskAssignment | TaskPlan | TaskImplementation | None:
    """Dispatch on ``payload['aux_type']`` to construct the target row."""
    payload = pending.payload
    aux_type = payload.get("aux_type")
    entity_id = pending.entity_id
    if aux_type == "approval":
        return Approval(
            task_id=entity_id,
            stage=payload["stage"],
            decision=payload["decision"],
            decided_by=payload["decided_by"],
            decided_by_role=payload["decided_by_role"],
            feedback=payload.get("feedback"),
        )
    if aux_type == "task_assignment":
        return TaskAssignment(
            task_id=entity_id,
            assignee_type=payload["assignee_type"],
            assignee_id=payload["assignee_id"],
            assigned_by=payload["assigned_by"],
        )
    if aux_type == "task_plan":
        return TaskPlan(
            task_id=entity_id,
            plan_path=payload["plan_path"],
            plan_sha=payload["plan_sha"],
            submitted_by=payload["submitted_by"],
        )
    if aux_type == "task_implementation":
        return TaskImplementation(
            task_id=entity_id,
            pr_url=payload.get("pr_url"),
            commit_sha=payload["commit_sha"],
            summary=payload["summary"],
            submitted_by=payload["submitted_by"],
        )
    return None


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


async def _dispatch_effectors(
    db: AsyncSession,
    event: LifecycleWebhookEvent,
    workflow_name: str,
    registry: EffectorRegistry,
    settings: Settings,
    correlation_id: uuid.UUID | None,
) -> None:
    """Fire ``registry.fire_all`` for the transition resolved from *event*.

    Looks up the local entity by ``engine_item_id`` to populate
    :attr:`EffectorContext.entity_id` — effectors expect the *local* UUID,
    not the engine's. Cache miss is logged + skipped (mirrors
    :func:`_update_status_cache`).
    """
    to_status = event.data.to_status
    from_status = event.data.from_status
    if to_status is None:
        return

    entity_type: Literal["work_item", "task"]
    if workflow_name == declarations.TASK_WORKFLOW_NAME:
        entity_type = "task"
        task = await db.scalar(
            select(Task).where(Task.engine_item_id == event.item_id)
        )
        entity_id = task.id if task is not None else None
    elif workflow_name == declarations.WORK_ITEM_WORKFLOW_NAME:
        entity_type = "work_item"
        wi = await db.scalar(
            select(WorkItem).where(WorkItem.engine_item_id == event.item_id)
        )
        entity_id = wi.id if wi is not None else None
    else:
        return

    if entity_id is None:
        logger.info(
            "effector dispatch: %s engine_item_id=%s not found locally; skipping",
            entity_type,
            event.item_id,
        )
        return

    transition = build_transition_key(entity_type, from_status, to_status)
    ctx = EffectorContext(
        entity_type=entity_type,
        entity_id=entity_id,
        from_state=from_status,
        to_state=to_status,
        transition=transition,
        correlation_id=correlation_id,
        db=db,
        settings=settings,
    )
    await registry.fire_all(ctx)


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
