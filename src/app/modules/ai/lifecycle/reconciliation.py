"""Outbox reconciliation — drain orphaned ``PendingAuxWrite`` rows.

A ``PendingAuxWrite`` is enqueued inside the signal adapter's transaction
alongside the engine mirror + idempotency key (FEAT-008/T-167). The
reactor consumes the row on the engine's ``item.transitioned`` webhook.
When that webhook is lost — engine outage, network drop, subscription
misconfig — the pending row is orphaned and the aux audit write (Approval,
TaskAssignment, TaskPlan, TaskImplementation) never lands.

This module walks the outbox, queries the engine for the current state of
each pending signal's target item, and materializes the aux row when the
engine confirms the transition landed server-side. Idempotent: successful
rows are deleted from the outbox; re-running finds nothing to do.

Design calls:
- **Stale rows preserved, not deleted.** If the engine says the transition
  didn't land, the pending row stays so an operator investigates. Silently
  deleting the breadcrumb is worse than leaving it.
- **Rejection signals materialize unconditionally.** Their semantics are
  "record the rejection" — the engine has no state change to check.
- **Unknown items are a third bucket.** Distinguishes "engine lost the
  signal" from "engine lost the item" so operators read the report right.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient
from app.modules.ai.lifecycle.reactor import build_aux_row
from app.modules.ai.models import PendingAuxWrite, Task, WorkItem

logger = logging.getLogger(__name__)

_RECONCILE_OUTCOMES = Literal["materialized", "stale", "unknown"]

# Signal → target engine state. ``None`` means the signal doesn't advance
# state (rejections); reconcile materializes unconditionally for those.
_SIGNAL_TARGET_STATE: dict[str, str | None] = {
    "approve-task": "assigning",
    "assign-task": "planning",
    "submit-plan": "plan_review",
    "approve-plan": "implementing",
    "submit-implementation": "impl_review",
    "approve-review": "done",
    "defer-task": "deferred",
    # Rejections — no engine-side state change:
    "reject-task": None,
    "reject-plan": None,
    "reject-review": None,
}


@dataclass(slots=True)
class ReconciliationReport:
    """Bucketed outcome of a reconciliation run."""

    scanned: int = 0
    materialized: int = 0
    skipped_stale: int = 0
    skipped_unknown: int = 0
    errors: list[str] = field(default_factory=list[str])


async def reconcile(
    db: AsyncSession,
    engine: FlowEngineLifecycleClient,
    *,
    since: timedelta | None = None,
    dry_run: bool = False,
) -> ReconciliationReport:
    """Drain orphan ``PendingAuxWrite`` rows by querying engine state.

    *since* bounds the scan to rows enqueued within the given window. When
    ``None``, scans the full outbox. *dry_run* suppresses DB writes and
    commit; the report still reflects what a real run would do.

    Returns a :class:`ReconciliationReport`. Errors mid-loop are captured
    and the loop continues — one bad row does not abort the drain.
    """
    query = select(PendingAuxWrite).order_by(PendingAuxWrite.enqueued_at)
    if since is not None:
        query = query.where(
            PendingAuxWrite.enqueued_at >= datetime.now(UTC) - since
        )
    rows = list(await db.scalars(query))

    report = ReconciliationReport()
    for pending in rows:
        report.scanned += 1
        try:
            outcome = await _reconcile_one(db, engine, pending, dry_run=dry_run)
        except Exception as exc:
            report.errors.append(f"{pending.correlation_id}: {exc}")
            logger.exception(
                "reconcile row failed",
                extra={"correlation_id": str(pending.correlation_id)},
            )
            continue
        if outcome == "materialized":
            report.materialized += 1
        elif outcome == "stale":
            report.skipped_stale += 1
        else:
            report.skipped_unknown += 1

    if not dry_run:
        await db.commit()
    return report


async def _reconcile_one(
    db: AsyncSession,
    engine: FlowEngineLifecycleClient,
    pending: PendingAuxWrite,
    *,
    dry_run: bool,
) -> _RECONCILE_OUTCOMES:
    if pending.entity_type == "task":
        entity = await db.scalar(select(Task).where(Task.id == pending.entity_id))
    else:
        entity = await db.scalar(
            select(WorkItem).where(WorkItem.id == pending.entity_id)
        )
    if entity is None or entity.engine_item_id is None:
        return "unknown"

    target_state = _SIGNAL_TARGET_STATE.get(pending.signal_name)
    if target_state is None:
        # Rejection (or unmapped signal): materialize unconditionally —
        # the engine had no work to do but our outbox row still resolves.
        if not dry_run:
            await _apply(db, pending)
        return "materialized"

    engine_state = await engine.get_item_state(entity.engine_item_id)
    if engine_state is None:
        return "unknown"
    if engine_state == target_state:
        if not dry_run:
            await _apply(db, pending)
        return "materialized"
    return "stale"


async def _apply(db: AsyncSession, pending: PendingAuxWrite) -> None:
    """Materialize the aux row (if the aux_type maps) and drop the pending."""
    aux_row = build_aux_row(pending)
    if aux_row is not None:
        db.add(aux_row)
    else:
        logger.warning(
            "pending_aux_write has unknown aux_type=%r; dropping without aux row",
            pending.payload.get("aux_type"),
            extra={"correlation_id": str(pending.correlation_id)},
        )
    await db.delete(pending)
    await db.flush()


def format_report(report: ReconciliationReport, *, dry_run: bool) -> str:
    """Human-readable summary for CLI output."""
    lines = [
        f"Reconciliation report (dry-run: {str(dry_run).lower()})",
        f"  Scanned:           {report.scanned}",
        f"  Materialized:      {report.materialized}",
        f"  Skipped (stale):   {report.skipped_stale}",
        f"  Skipped (unknown): {report.skipped_unknown}",
        f"  Errors:            {len(report.errors)}",
    ]
    for err in report.errors:
        lines.append(f"    ! {err}")
    return "\n".join(lines)
