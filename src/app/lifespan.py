"""App lifespan: supervisor lifecycle + zombie-run reconciliation (T-045).

On startup we flip any ``running`` rows left over from a prior process
into ``failed/error`` so the on-disk state stops lying.  On shutdown we
drain the in-process :class:`RunSupervisor` within a grace window.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.database import get_engine, make_sessionmaker
from app.modules.ai.enums import RunStatus, StopReason
from app.modules.ai.models import Run

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.ai.supervisor import RunSupervisor

logger = logging.getLogger(__name__)


async def reconcile_zombie_runs(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Flip every ``running`` row to ``failed/error`` with a zombie marker.

    Returns the number of rows updated.  Called once from the lifespan
    startup hook — a restarted process means any row left in ``running`` is
    orphaned by definition (the supervisor lives in-process).
    """
    now = datetime.now(UTC)
    async with session_factory() as session:
        zombies = await session.scalars(
            select(Run).where(Run.status == RunStatus.RUNNING)
        )
        count = 0
        for run in zombies:
            existing = dict(run.final_state or {})
            existing["zombie_reason"] = "process restart"
            run.final_state = existing
            count += 1

        if count == 0:
            return 0

        await session.execute(
            update(Run)
            .where(Run.status == RunStatus.RUNNING)
            .values(
                status=RunStatus.FAILED,
                stop_reason=StopReason.ERROR,
                ended_at=now,
            )
        )
        await session.commit()

    logger.info("zombie reconciliation: transitioned %d run(s) to failed/error", count)
    return count


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan: wire the supervisor, run the zombie sweep, drain on shutdown."""
    from app.modules.ai.supervisor import RunSupervisor

    session_factory = make_sessionmaker(get_engine())

    # Bind a fresh supervisor onto app state for request-scoped access.
    supervisor: RunSupervisor = RunSupervisor()
    app.state.supervisor = supervisor

    try:
        await reconcile_zombie_runs(session_factory)
    except Exception:
        logger.exception("zombie reconciliation failed; continuing startup")

    try:
        yield
    finally:
        await supervisor.shutdown(grace=5.0)
        logger.info("supervisor drained on shutdown")
