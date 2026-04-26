"""Restart reconciler for orphan dispatches (FEAT-009 / T-221).

A dispatch in ``pending`` or ``dispatched`` state with no in-process
:class:`~app.modules.ai.supervisor.RunSupervisor` future is *orphaned*
— the orchestrator that originally registered it has died, and no
amount of webhook delivery will ever wake it.

On startup we walk the table and mark every such row ``cancelled``
with ``detail='orchestrator_restart'`` so the owning runs can be
zombie-reconciled (already handled by ``reconcile_zombie_runs``).

The conservative-by-design choice in v0.4.0 is to *never* call out
to a remote executor's health endpoint here — we lack the per-executor
config to do that safely, and a stale dispatch row blocking forever
is worse than over-cancelling on restart.  A richer recovery path
(query-then-decide) is a future FEAT once a real second executor
exists.

A CLI command (``uv run orchestrator reconcile-dispatches``) for
on-demand reruns is deferred to a follow-on PR — the lifespan-time
auto-reconcile is the critical path.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.ai.enums import DispatchState
from app.modules.ai.models import Dispatch

logger = logging.getLogger(__name__)


async def reconcile_orphan_dispatches(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Mark every non-terminal ``Dispatch`` row ``cancelled``.

    Called once from the lifespan startup hook.  Returns the number of
    rows transitioned.  Idempotent — a second invocation finds zero
    non-terminal rows and returns 0.
    """
    now = datetime.now(UTC)
    async with session_factory() as session:
        rows = (
            await session.scalars(
                select(Dispatch).where(Dispatch.state.in_([DispatchState.PENDING, DispatchState.DISPATCHED]))
            )
        ).all()
        if not rows:
            return 0

        for dispatch in rows:
            try:
                dispatch.mark_cancelled(at=now, detail="orchestrator_restart")
            except Exception:
                logger.warning(
                    "could not cancel orphan dispatch %s",
                    dispatch.dispatch_id,
                    exc_info=True,
                )
        await session.commit()

    logger.info(
        "executor reconciler: cancelled %d orphan dispatch(es) on startup",
        len(rows),
    )
    return len(rows)
