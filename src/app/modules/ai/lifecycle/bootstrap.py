"""Workflow bootstrap for FEAT-006 rc2 (T-129) + BUG-002 (tenant scope).

At orchestrator startup, ensure the two workflows declared in
:mod:`app.modules.ai.lifecycle.declarations` exist in the flow engine
under the configured tenant.

Cold start: create each; cache the engine-side id keyed by
``(tenant_id, name)``. Subsequent starts: read from cache, validate
the cached id with a cheap engine ``GET /workflows/<id>`` call, and
re-resolve transparently if the engine 404s (covers tenant-change
recovery and in-tenant data resets).

Cache miss + engine ``409 name exists``: look up the id by name and
upsert locally (covers the case where the cache was wiped but the
engine still has the workflow).

Design choice: declarations are Python constants, not a per-project
config file.  When project-specific workflows become a thing, move the
declarations into a ``lifecycle_declarations`` table or a YAML file and
re-run this bootstrap.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import EngineError
from app.modules.ai.lifecycle import declarations
from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient
from app.modules.ai.models import EngineWorkflow

logger = logging.getLogger(__name__)


async def ensure_workflows(
    db: AsyncSession,
    client: FlowEngineLifecycleClient,
    *,
    tenant_id: uuid.UUID,
) -> dict[str, uuid.UUID]:
    """Ensure every declared workflow exists in the engine for *tenant_id*.

    Returns a mapping ``{workflow_name: engine_workflow_id}`` for the
    caller to stash in app state. Idempotent across restarts; recovers
    from stale cache rows (tenant change, engine data reset) by
    validating each cache hit with a cheap engine round-trip.
    """
    result: dict[str, uuid.UUID] = {}
    for decl in declarations.ALL_WORKFLOWS:
        name: str = decl["name"]
        workflow_id = await _resolve(db, client, decl, tenant_id=tenant_id)
        result[name] = workflow_id
    return result


async def _resolve(
    db: AsyncSession,
    client: FlowEngineLifecycleClient,
    decl: dict[str, Any],
    *,
    tenant_id: uuid.UUID,
) -> uuid.UUID:
    name = decl["name"]

    # Cache lookup is keyed by (tenant_id, name) — see BUG-002 for the
    # original tenant-blind bug history.
    cached = await db.scalar(
        select(EngineWorkflow.engine_workflow_id).where(
            EngineWorkflow.tenant_id == tenant_id,
            EngineWorkflow.name == name,
        )
    )
    if cached is not None:
        if await _engine_recognizes(client, cached):
            logger.debug("workflow %s resolved from cache: %s", name, cached)
            return cached
        # Stale cache: engine doesn't know this id under the current tenant.
        # Drop the row and fall through to create-or-409-lookup recovery.
        logger.warning(
            "stale engine_workflows row tenant=%s name=%s old_id=%s — re-resolving",
            tenant_id,
            name,
            cached,
        )
        await db.execute(
            delete(EngineWorkflow).where(
                EngineWorkflow.tenant_id == tenant_id,
                EngineWorkflow.name == name,
            )
        )
        await db.commit()

    try:
        engine_id = await client.create_workflow(
            name=name,
            statuses=decl["statuses"],
            transitions=decl["transitions"],
            initial_status=decl["initial_status"],
        )
        logger.info("workflow %s created in engine: %s", name, engine_id)
    except EngineError as exc:
        if exc.engine_http_status != 409:
            raise
        existing = await client.get_workflow_by_name(name)
        if existing is None:
            raise EngineError(
                f"engine reported 409 for workflow {name} but lookup returned None",
            ) from exc
        engine_id = existing
        logger.info("workflow %s already exists in engine: %s", name, engine_id)

    await _upsert_cache(db, tenant_id=tenant_id, name=name, engine_id=engine_id)
    await db.commit()
    return engine_id


async def _engine_recognizes(
    client: FlowEngineLifecycleClient, engine_id: uuid.UUID
) -> bool:
    """Return False on engine 404 (stale cache); re-raise other errors.

    A 5xx or transient failure here surfaces to the lifespan and fails
    the boot — that's the right behavior since the orchestrator can't
    operate without valid workflow ids.
    """
    try:
        return await client.get_workflow_by_id(engine_id)
    except EngineError as exc:
        if exc.engine_http_status == 404:
            return False
        raise


async def _upsert_cache(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    engine_id: uuid.UUID,
) -> None:
    stmt = (
        pg_insert(EngineWorkflow)
        .values(tenant_id=tenant_id, name=name, engine_workflow_id=engine_id)
        .on_conflict_do_nothing(index_elements=["tenant_id", "name"])
    )
    await db.execute(stmt)
