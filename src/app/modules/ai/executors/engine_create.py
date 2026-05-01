"""Engine-creation executor (BUG-003).

Sibling of :class:`EngineExecutor`, but for the engine's W1 ``create_item``
path rather than its W2-W6 / T1-T12 ``transition_item`` paths.
``CompositeLLMEngineExecutor`` and :class:`EngineExecutor` both assume an
``engineItemId`` is present in dispatch intake — for ``load_work_item``
that is by definition false, because W1 is the call that *creates* the
engine item.

Wire-shape:

1. Read the work-item brief from :class:`LifecycleMemory.work_item` (the
   prior :class:`LLMContentExecutor` step persisted ``id``, ``title``).
2. Call ``lifecycle_client.create_item(workflow_id=..., title=...,
   external_ref=...)`` synchronously — ``create_item`` returns the new
   engine item id directly (no webhook round-trip).
3. In one transaction: insert the local ``WorkItem`` row + write
   ``engineItemId`` into ``Run.intake`` so downstream dispatches read it
   via :func:`_build_node_intake` like every other engine-bound node.
4. Return a ``completed`` local-mode envelope with
   ``result={"engineItemId": <uuid>, "workItemId": <uuid>}``.

Mode is ``"local"`` (not ``"engine"``).  W1 is a synchronous create that
does not produce an ``item.transitioned`` webhook — there is nothing
for the wake-on-correlation pipeline to do.  The envelope is terminal at
return time, mirroring the :class:`LocalExecutor` contract.

Import quarantine: the :class:`FlowEngineLifecycleClient` reaches the
executor via constructor injection — only the type is imported under
:data:`typing.TYPE_CHECKING`, preserving the FEAT-009 / FEAT-010
discipline that the deterministic runtime never transitively pulls the
engine HTTP client into ``sys.modules``.
"""

from __future__ import annotations

import copy
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from app.core.exceptions import EngineError
from app.modules.ai.executors.base import DispatchContext, ExecutorMode
from app.modules.ai.models import Run, RunMemory, WorkItem
from app.modules.ai.schemas import DispatchEnvelope
from app.modules.ai.tools.lifecycle.memory import (
    LIFECYCLE_MEMORY_NS,
    read_lifecycle_memory,
)

if TYPE_CHECKING:
    from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient


logger = logging.getLogger(__name__)


class EngineCreateExecutor:
    """Local executor that creates the engine work item (W1).

    Constructor takes the engine ``workflow_id`` directly — ``register_lifecycle_v03``
    resolves it once at lifespan time from ``app.state.lifecycle_workflow_ids``.
    The executor does not re-resolve at dispatch time; tenant rotation is
    a lifespan-restart concern (mirrors ``EngineExecutor``).
    """

    mode: ClassVar[ExecutorMode] = "local"

    def __init__(
        self,
        ref: str,
        *,
        workflow_id: uuid.UUID,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
        opened_by: str = "lifecycle-agent",
    ) -> None:
        self.name = ref
        self._ref = ref
        self._workflow_id = workflow_id
        self._client = lifecycle_client
        self._session_factory = session_factory
        self._opened_by = opened_by

    async def dispatch(self, ctx: DispatchContext) -> DispatchEnvelope:
        started = datetime.now(UTC)

        external_ref, title, wi_type, source_path = await self._read_brief(ctx.run_id)
        if not external_ref or not title:
            return self._failed(
                ctx,
                started=started,
                detail=(
                    "register_work_item: lifecycle.v1 memory missing work_item brief; "
                    "expected load_work_item to have populated work_item.id and .title — "
                    f"got external_ref={external_ref!r} title={title!r}"
                ),
            )

        try:
            engine_item_id = await self._client.create_item(
                workflow_id=self._workflow_id,
                title=title,
                external_ref=external_ref,
                metadata={"type": wi_type, "source_path": source_path or ""},
            )
        except EngineError as exc:
            return self._failed(
                ctx,
                started=started,
                detail=f"engine_error: {exc}",
            )
        except Exception as exc:
            logger.exception(
                "register_work_item: create_item raised unexpectedly",
                extra={"dispatch_id": str(ctx.dispatch_id)},
            )
            return self._failed(
                ctx,
                started=started,
                detail=f"{type(exc).__name__}: {exc}",
            )

        try:
            local_work_item_id = await self._persist_work_item(
                run_id=ctx.run_id,
                engine_item_id=engine_item_id,
                external_ref=external_ref,
                wi_type=wi_type,
                title=title,
                source_path=source_path,
            )
        except Exception as exc:
            logger.exception(
                "register_work_item: local persistence failed after create_item ok",
                extra={"dispatch_id": str(ctx.dispatch_id)},
            )
            return self._failed(
                ctx,
                started=started,
                detail=f"local_persist_failed: {type(exc).__name__}: {exc}",
            )

        return DispatchEnvelope(
            dispatch_id=ctx.dispatch_id,
            step_id=ctx.step_id,
            run_id=ctx.run_id,
            executor_ref=self._ref,
            mode="local",  # type: ignore[arg-type]
            state="completed",  # type: ignore[arg-type]
            intake=dict(ctx.intake),
            outcome="ok",  # type: ignore[arg-type]
            result={
                "engineItemId": str(engine_item_id),
                "workItemId": str(local_work_item_id),
            },
            started_at=started,
            finished_at=datetime.now(UTC),
        )

    async def _read_brief(
        self, run_id: uuid.UUID
    ) -> tuple[str, str, str, str | None]:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(RunMemory).where(RunMemory.run_id == run_id)
            )
        memory = read_lifecycle_memory((row.data if row is not None else None) or {})
        wi = memory.work_item
        if wi is None:
            return ("", "", "FEAT", None)
        return (wi.id, wi.title, wi.type, wi.path or None)

    async def _persist_work_item(
        self,
        *,
        run_id: uuid.UUID,
        engine_item_id: uuid.UUID,
        external_ref: str,
        wi_type: str,
        title: str,
        source_path: str | None,
    ) -> uuid.UUID:
        async with self._session_factory() as session, session.begin():
            existing = await session.scalar(
                select(WorkItem).where(WorkItem.engine_item_id == engine_item_id)
            )
            if existing is None:
                wi = WorkItem(
                    external_ref=external_ref,
                    type=wi_type,
                    title=title,
                    source_path=source_path,
                    status="open",
                    opened_by=self._opened_by,
                    engine_item_id=engine_item_id,
                )
                session.add(wi)
                await session.flush()
                local_id = wi.id
            else:
                local_id = existing.id

            run_row = await session.get(Run, run_id)
            if run_row is not None:
                merged: dict[str, Any] = copy.deepcopy(run_row.intake or {}) or {}
                merged["engineItemId"] = str(engine_item_id)
                merged["workItemId"] = external_ref
                run_row.intake = merged
                flag_modified(run_row, "intake")

        return local_id

    def _failed(
        self,
        ctx: DispatchContext,
        *,
        started: datetime,
        detail: str,
    ) -> DispatchEnvelope:
        return DispatchEnvelope(
            dispatch_id=ctx.dispatch_id,
            step_id=ctx.step_id,
            run_id=ctx.run_id,
            executor_ref=self._ref,
            mode="local",  # type: ignore[arg-type]
            state="failed",  # type: ignore[arg-type]
            intake=dict(ctx.intake),
            outcome="error",  # type: ignore[arg-type]
            detail=detail,
            started_at=started,
            finished_at=datetime.now(UTC),
        )


# Mark unused import for static analysis if needed.
_ = LIFECYCLE_MEMORY_NS

__all__ = ["EngineCreateExecutor"]
