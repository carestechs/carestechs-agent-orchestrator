"""Restart reconciler for orphan dispatches (FEAT-009 / T-221, FEAT-010 / T-235).

A dispatch in ``pending`` or ``dispatched`` state with no in-process
:class:`~app.modules.ai.supervisor.RunSupervisor` future is *orphaned*
— the orchestrator that originally registered it has died, and no
amount of webhook delivery will ever wake it.

There are two reconciliation paths, keyed on ``Dispatch.mode``:

1. **local / remote / human** (FEAT-009 / T-221).  Conservative cancel —
   the row is marked ``cancelled`` with ``detail='orchestrator_restart'``
   so the owning runs can be zombie-reconciled.  We never call out to
   a remote executor's health endpoint here — we lack the per-executor
   config to do that safely, and a stale dispatch row blocking forever
   is worse than over-cancelling on restart.

2. **engine** (FEAT-010 / T-235).  Engine dispatches carry a
   ``correlation_id`` (in ``Dispatch.intake``) and a matching outbox row
   in ``pending_aux_writes``.  We query the engine for the item's
   current state via :meth:`FlowEngineLifecycleClient.get_item_state`:

   - If the engine confirms the transition occurred (current status ==
     the dispatch's ``to_status``), the run owner is gone but the engine
     state is correct — mark the dispatch ``failed`` with
     ``detail="orchestrator_restart_engine_confirmed"``.  The outbox row
     stays for a future ``reconcile-aux`` (or the next webhook arrival)
     to materialise the aux.

   - If the engine reports a mismatch, mark the dispatch ``failed`` with
     ``detail="orchestrator_restart_engine_did_not_transition"``.  The
     outbox row stays in place — a future operator may investigate.

   - If no engine client is available (engine-absent dev mode, or no
     read API), every orphan engine dispatch is conservatively marked
     ``failed`` with ``detail="orchestrator_restart_engine_unconfirmed"``
     and the outbox is preserved.  This is the safe fallback: a future
     run of ``reconcile-aux`` (or a later webhook) can still settle the
     aux row.

The CLI command ``uv run orchestrator reconcile-dispatches`` runs this
reconciler on demand (companion to ``reconcile-aux``).  The lifespan
hook continues to invoke :func:`reconcile_orphan_dispatches` at startup
without an engine client — keeping startup fast and avoiding a hard
dependency on engine availability for boot.  Operators run the CLI when
they need engine-confirmed reconciliation.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.ai.enums import DispatchMode, DispatchState, RunStatus
from app.modules.ai.models import Dispatch, PendingAuxWrite, Run

if TYPE_CHECKING:
    from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient

logger = logging.getLogger(__name__)


# Detail strings used by the engine-mode branch — referenced by tests +
# operator docs.  Keep these stable; they're effectively a contract.
ENGINE_DETAIL_CONFIRMED = "orchestrator_restart_engine_confirmed"
ENGINE_DETAIL_DID_NOT_TRANSITION = "orchestrator_restart_engine_did_not_transition"
ENGINE_DETAIL_UNCONFIRMED = "orchestrator_restart_engine_unconfirmed"


@dataclass(slots=True)
class DispatchReconcileReport:
    """Bucketed outcome of an engine-aware reconcile run."""

    scanned: int = 0
    cancelled_non_engine: int = 0
    engine_confirmed: int = 0
    engine_did_not_transition: int = 0
    engine_unconfirmed: int = 0
    skipped_run_alive: int = 0
    skipped_already_terminal: int = 0
    errors: list[str] = field(default_factory=list[str])


async def reconcile_orphan_dispatches(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Mark every non-terminal ``Dispatch`` row ``cancelled``.

    Lifespan-time entrypoint preserved for FEAT-009 backward
    compatibility (the lifespan hook still calls this without an engine
    client).  At lifespan startup, every non-terminal row is by
    definition orphaned (a fresh process has no in-flight supervisor),
    so the run-alive filter is bypassed and every non-engine row is
    cancelled, every engine row is conservatively marked ``failed``
    with ``detail=ENGINE_DETAIL_UNCONFIRMED``.  Operators who need
    engine-confirmed reconciliation should run
    ``orchestrator reconcile-dispatches`` after startup.

    Returns the count of rows transitioned (cancelled + failed).
    """
    report = await reconcile_orphan_dispatches_engine_aware(
        session_factory,
        engine_client=None,
        since=None,
        dry_run=False,
        skip_run_alive=False,
    )
    return (
        report.cancelled_non_engine
        + report.engine_confirmed
        + report.engine_did_not_transition
        + report.engine_unconfirmed
    )


async def reconcile_orphan_dispatches_engine_aware(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    engine_client: FlowEngineLifecycleClient | None,
    since: timedelta | None = None,
    dry_run: bool = False,
    skip_run_alive: bool = True,
) -> DispatchReconcileReport:
    """Reconcile orphan dispatches, handling engine mode specially.

    For each non-terminal dispatch:

    * If ``skip_run_alive`` is ``True`` (default — the CLI path) and the
      owning run is still ``running``, skip — the dispatch is not
      orphaned, just in-flight.  At lifespan startup
      (``skip_run_alive=False``), every non-terminal row is treated as
      orphaned because a fresh process has no in-flight supervisor.
    * If ``mode == "engine"``: query the engine (when ``engine_client``
      is provided) and settle per the rules in the module docstring.
    * Otherwise: cancel with ``detail="orchestrator_restart"`` (FEAT-009
      conservative path, preserved bit-for-bit).

    *since* bounds the scan to dispatches whose ``started_at`` is within
    the window.  *dry_run* records what would happen without writing.

    Returns a :class:`DispatchReconcileReport`.  Errors on individual
    rows are captured and logged; the loop continues.
    """
    report = DispatchReconcileReport()
    now = datetime.now(UTC)

    async with session_factory() as session:
        query = select(Dispatch).where(Dispatch.state.in_([DispatchState.PENDING, DispatchState.DISPATCHED]))
        if since is not None:
            query = query.where(Dispatch.started_at >= now - since)

        rows = list(await session.scalars(query))
        report.scanned = len(rows)
        if not rows:
            return report

        # Pre-load owning Run statuses so we don't hit the DB per row.
        run_ids = {row.run_id for row in rows}
        run_statuses: dict[uuid.UUID, str] = {
            r.id: r.status for r in (await session.scalars(select(Run).where(Run.id.in_(run_ids))))
        }

        for dispatch in rows:
            # Defensive guard — tolerate any individual-row failure.
            try:
                run_status = run_statuses.get(dispatch.run_id)
                if skip_run_alive and run_status == RunStatus.RUNNING:
                    # In-flight run — leave the dispatch alone.
                    report.skipped_run_alive += 1
                    continue

                if dispatch.state not in (
                    DispatchState.PENDING,
                    DispatchState.DISPATCHED,
                ):
                    # Already terminal (race with another reconciler / late
                    # webhook).  Skip silently.
                    report.skipped_already_terminal += 1
                    continue

                if dispatch.mode == DispatchMode.ENGINE:
                    await _reconcile_engine_dispatch(
                        session,
                        dispatch,
                        engine_client=engine_client,
                        now=now,
                        dry_run=dry_run,
                        report=report,
                    )
                else:
                    if not dry_run:
                        dispatch.mark_cancelled(at=now, detail="orchestrator_restart")
                    report.cancelled_non_engine += 1
            except Exception as exc:
                report.errors.append(f"{dispatch.dispatch_id}: {exc}")
                logger.exception(
                    "could not reconcile orphan dispatch %s",
                    dispatch.dispatch_id,
                    extra={
                        "dispatch_id": str(dispatch.dispatch_id),
                        "mode": dispatch.mode,
                    },
                )
                continue

        if not dry_run:
            await session.commit()

    logger.info(
        "dispatch reconciler: scanned=%d cancelled_non_engine=%d "
        "engine_confirmed=%d engine_did_not_transition=%d "
        "engine_unconfirmed=%d skipped_run_alive=%d "
        "skipped_already_terminal=%d errors=%d",
        report.scanned,
        report.cancelled_non_engine,
        report.engine_confirmed,
        report.engine_did_not_transition,
        report.engine_unconfirmed,
        report.skipped_run_alive,
        report.skipped_already_terminal,
        len(report.errors),
    )
    return report


async def _reconcile_engine_dispatch(
    session: AsyncSession,
    dispatch: Dispatch,
    *,
    engine_client: FlowEngineLifecycleClient | None,
    now: datetime,
    dry_run: bool,
    report: DispatchReconcileReport,
) -> None:
    """Settle one engine-mode orphan dispatch.

    See module docstring for the three branches.  Outbox rows are never
    deleted by this function — ``reconcile-aux`` is the path that
    materialises (and drains) outbox rows.  Here we only settle the
    *dispatch* row.
    """
    intake = dispatch.intake or {}
    correlation_raw = intake.get("correlation_id")
    to_status_raw = intake.get("to_status")

    # Recover correlation_id + to_status from the outbox row when the
    # dispatch's intake is missing them (older rows pre-T-234 may not
    # carry them; the outbox is the durable source of truth).
    correlation_id: uuid.UUID | None = None
    if correlation_raw is not None:
        try:
            correlation_id = uuid.UUID(str(correlation_raw))
        except ValueError:
            correlation_id = None

    outbox_row: PendingAuxWrite | None = None
    if correlation_id is not None:
        outbox_row = await session.scalar(
            select(PendingAuxWrite).where(PendingAuxWrite.correlation_id == correlation_id)
        )

    to_status: str | None = str(to_status_raw) if to_status_raw is not None else None
    if to_status is None and outbox_row is not None:
        payload_to_status = (outbox_row.payload or {}).get("to_status")
        if payload_to_status is not None:
            to_status = str(payload_to_status)

    # Branch 1: no engine client OR no outbox / no to_status to compare
    # against — conservative path.
    if engine_client is None or outbox_row is None or to_status is None:
        if not dry_run:
            dispatch.mark_failed(at=now, detail=ENGINE_DETAIL_UNCONFIRMED)
        report.engine_unconfirmed += 1
        return

    # Branch 2/3: ask the engine what state the item is in.
    item_id = outbox_row.entity_id
    try:
        engine_state = await engine_client.get_item_state(item_id)
    except Exception:
        # Engine read failed — fall back to conservative-cancel.  We log
        # but do not raise; the caller's per-row try/except still wraps
        # this for safety.
        logger.warning(
            "engine get_item_state failed for dispatch %s item %s; " "falling back to unconfirmed",
            dispatch.dispatch_id,
            item_id,
            exc_info=True,
        )
        if not dry_run:
            dispatch.mark_failed(at=now, detail=ENGINE_DETAIL_UNCONFIRMED)
        report.engine_unconfirmed += 1
        return

    if engine_state == to_status:
        if not dry_run:
            dispatch.mark_failed(at=now, detail=ENGINE_DETAIL_CONFIRMED)
        report.engine_confirmed += 1
    else:
        if not dry_run:
            dispatch.mark_failed(at=now, detail=ENGINE_DETAIL_DID_NOT_TRANSITION)
        report.engine_did_not_transition += 1


def format_dispatch_report(report: DispatchReconcileReport, *, dry_run: bool) -> str:
    """Human-readable summary for the ``reconcile-dispatches`` CLI.

    Mirrors the shape of :func:`app.modules.ai.lifecycle.reconciliation.format_report`
    so operator output is consistent across the two reconcilers.
    """
    lines = [
        f"Dispatch reconciliation report (dry-run: {str(dry_run).lower()})",
        f"  Scanned:                      {report.scanned}",
        f"  Cancelled (non-engine):       {report.cancelled_non_engine}",
        f"  Engine confirmed:             {report.engine_confirmed}",
        f"  Engine did-not-transition:    {report.engine_did_not_transition}",
        f"  Engine unconfirmed:           {report.engine_unconfirmed}",
        f"  Skipped (run alive):          {report.skipped_run_alive}",
        f"  Skipped (already terminal):   {report.skipped_already_terminal}",
        f"  Errors:                       {len(report.errors)}",
    ]
    for err in report.errors:
        lines.append(f"    ! {err}")
    return "\n".join(lines)


# Re-exports for clarity.
__all__ = [
    "ENGINE_DETAIL_CONFIRMED",
    "ENGINE_DETAIL_DID_NOT_TRANSITION",
    "ENGINE_DETAIL_UNCONFIRMED",
    "DispatchReconcileReport",
    "format_dispatch_report",
    "reconcile_orphan_dispatches",
    "reconcile_orphan_dispatches_engine_aware",
]


# Type-only import side-effect avoidance — keeps modules importable on
# environments where ``Any`` isn't otherwise referenced.
_ = (Any,)
