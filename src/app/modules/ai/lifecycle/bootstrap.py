"""Workflow bootstrap for FEAT-006 rc2 (T-129).

At orchestrator startup, ensure the two workflows declared in
:mod:`app.modules.ai.lifecycle.declarations` exist in the flow engine.

Cold start: create each; cache the engine-side id in
``engine_workflows``.  Subsequent starts: read from cache, skip engine
calls.  Cache miss + engine ``409 name exists``: look up the id by name
and upsert locally (covers the case where the cache was wiped but the
engine still has the workflows).

Design choice: declarations are Python constants, not a per-project
config file.  When project-specific workflows become a thing, move the
declarations into a ``lifecycle_declarations`` table or a YAML file and
re-run this bootstrap.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
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
) -> dict[str, uuid.UUID]:
    """Ensure every declared workflow exists in the engine.

    Returns a mapping ``{workflow_name: engine_workflow_id}`` for the
    caller to stash in app state.  Idempotent across restarts.
    """
    result: dict[str, uuid.UUID] = {}
    for decl in declarations.ALL_WORKFLOWS:
        name: str = decl["name"]
        workflow_id = await _resolve(db, client, decl)
        result[name] = workflow_id
    return result


async def _resolve(
    db: AsyncSession,
    client: FlowEngineLifecycleClient,
    decl: dict[str, Any],
) -> uuid.UUID:
    name = decl["name"]

    cached = await db.scalar(
        select(EngineWorkflow.engine_workflow_id).where(EngineWorkflow.name == name)
    )
    if cached is not None:
        logger.debug("workflow %s resolved from cache: %s", name, cached)
        return cached

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

    await _upsert_cache(db, name, engine_id)
    await db.commit()
    return engine_id


async def _upsert_cache(
    db: AsyncSession, name: str, engine_id: uuid.UUID
) -> None:
    stmt = (
        pg_insert(EngineWorkflow)
        .values(name=name, engine_workflow_id=engine_id)
        .on_conflict_do_nothing(index_elements=["name"])
    )
    await db.execute(stmt)
