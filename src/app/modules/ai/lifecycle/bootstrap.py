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


# ---------------------------------------------------------------------------
# BUG-005 — webhook subscription registration
# ---------------------------------------------------------------------------


async def ensure_engine_subscriptions(
    client: FlowEngineLifecycleClient,
    *,
    workflow_ids: dict[str, uuid.UUID],
    public_base_url: str,
    webhook_secret: str,
) -> None:
    """Subscribe the orchestrator to ``item.transitioned`` for each workflow.

    Without this, the engine fires transitions but has no active webhook
    subscription to deliver them to — every ``EngineExecutor`` /
    ``CompositeLLMEngineExecutor`` / ``SequenceEngineExecutor`` dispatch
    would park on its supervisor future and time out.

    Idempotency strategy: the engine's POST creates a new subscription
    on every call (no server-side dedupe by url+event+workflow), so we
    GET the existing list per workflow and skip the POST when a row
    matches our (url, event_type) for that workflow.  Engine 409 is
    also tolerated as a defensive belt-and-braces for engines that do
    dedupe server-side.

    The callback URL is derived from ``Settings.public_base_url`` plus
    the existing ``/hooks/engine/lifecycle/item-transitioned`` route.
    Container deploys must set ``PUBLIC_BASE_URL`` to the orchestrator's
    container DNS name (e.g. ``http://orchestrator-api:8000``) so the
    engine can reach it on the shared network — ``localhost`` resolves
    to the engine itself from inside the engine container.
    """
    callback_url = public_base_url.rstrip("/") + "/hooks/engine/lifecycle/item-transitioned"
    event_type = "item.transitioned"
    for name, workflow_id in workflow_ids.items():
        try:
            existing = await client.list_webhook_subscriptions(workflow_id=workflow_id)
        except EngineError as exc:
            logger.warning(
                "could not list webhook subscriptions for workflow %s before subscribing "
                "(continuing with POST; engine 409 will still be tolerated): %s",
                name,
                exc,
            )
            existing = []

        if _subscription_matches(existing, url=callback_url, event_type=event_type):
            logger.info(
                "engine webhook subscription already present for workflow %s — skipping POST",
                name,
            )
            continue

        try:
            sub_id = await client.ensure_webhook_subscription(
                url=callback_url,
                event_type=event_type,
                workflow_id=workflow_id,
                secret=webhook_secret,
            )
            logger.info(
                "subscribed to engine %s webhook for workflow %s (subscription_id=%s, url=%s)",
                event_type,
                name,
                sub_id,
                callback_url,
            )
        except EngineError as exc:
            if exc.engine_http_status == 409:
                logger.info(
                    "engine reports webhook subscription already exists for workflow %s "
                    "(treating as success)",
                    name,
                )
                continue
            logger.error(
                "failed to register engine webhook subscription for workflow %s: %s",
                name,
                exc,
            )
            raise


def _subscription_matches(
    existing: list[dict[str, Any]],
    *,
    url: str,
    event_type: str,
) -> bool:
    """Return True if any row already matches our (url, event_type) pair.

    Tolerates the engine's response shape variations: ``url`` vs
    ``callbackUrl``, ``eventType`` vs ``event_type``.  workflow_id is
    pre-filtered by the caller's ``list_webhook_subscriptions`` call.
    """
    for row in existing:
        row_url = row.get("url") or row.get("callbackUrl")
        row_event = row.get("eventType") or row.get("event_type")
        if row_url == url and row_event == event_type:
            return True
    return False
