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

    # FEAT-006 rc2: ensure flow-engine workflows are registered.  Optional —
    # if lifecycle-engine config is absent we skip so dev setups without the
    # engine up still boot.
    await _bootstrap_lifecycle_workflows(app, session_factory)

    # FEAT-007: resolve the GitHub Checks client (App > PAT > Noop).
    _bootstrap_github_checks_client(app)

    try:
        yield
    finally:
        engine_client = getattr(app.state, "lifecycle_engine_client", None)
        if engine_client is not None:
            try:
                await engine_client.aclose()
            except Exception:
                logger.warning("lifecycle engine client close failed", exc_info=True)
        github_http = getattr(app.state, "github_http_client", None)
        if github_http is not None:
            try:
                await github_http.aclose()
            except Exception:
                logger.warning("github http client close failed", exc_info=True)
        await supervisor.shutdown(grace=5.0)
        logger.info("supervisor drained on shutdown")


def _bootstrap_github_checks_client(app: FastAPI) -> None:
    """Resolve the Checks client once and stash it on app state."""
    from app.config import get_settings
    from app.core.github import (
        get_github_checks_client,
        make_shared_http_client,
        resolved_strategy,
    )

    http = make_shared_http_client()
    client = get_github_checks_client(get_settings(), http)
    app.state.github_http_client = http
    app.state.github_checks_client = client
    logger.info("github checks strategy: %s", resolved_strategy(client))


async def _bootstrap_lifecycle_workflows(
    app: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Register FEAT-006 workflows in the flow engine on cold start."""
    from app.config import get_settings
    from app.modules.ai.lifecycle.bootstrap import ensure_workflows
    from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient

    settings = get_settings()
    base_url = settings.flow_engine_lifecycle_base_url
    api_key = settings.flow_engine_tenant_api_key
    if base_url is None or api_key is None:
        logger.info(
            "lifecycle engine not configured; skipping workflow bootstrap"
        )
        app.state.lifecycle_engine_client = None
        app.state.lifecycle_workflow_ids = {}
        return

    client = FlowEngineLifecycleClient(
        base_url=str(base_url),
        api_key=api_key.get_secret_value(),
    )
    app.state.lifecycle_engine_client = client

    try:
        async with session_factory() as session:
            workflow_ids = await ensure_workflows(session, client)
    except Exception:
        logger.exception("lifecycle workflow bootstrap failed; continuing startup")
        app.state.lifecycle_workflow_ids = {}
        return

    app.state.lifecycle_workflow_ids = workflow_ids
    logger.info(
        "lifecycle workflow bootstrap complete: %s",
        {name: str(wid) for name, wid in workflow_ids.items()},
    )
