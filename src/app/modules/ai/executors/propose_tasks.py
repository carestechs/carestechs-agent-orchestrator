"""T1xN task proposal + T2+T4 self-approve + W2 advance executor (BUG-004).

The v0.3.0 lifecycle was missing actual engine task entities — the
``generate_tasks`` composite tried to drive a single ``transition_item``
call against the work-item id with a task-shaped status, which the
engine rejects.  v0.1.0's pattern (in ``modules/ai/lifecycle/tasks.py``)
creates one engine entity per task via ``create_item(task_workflow, …)``
and then approves each via T2+T4 (``proposed → approved → assigning``)
before flipping the work item to ``in_progress`` (W2 — the
"approve-first-task" derivation).

This executor reproduces that shape behind the FEAT-009 seam.  It runs
synchronously (mode ``"local"``) — every engine call here is a
fire-and-await, the engine validates state transitions in-band, and
the dispatch returns a terminal ``completed`` envelope when the
sequence finishes.

Notable departures from the v0.1.0 path (carried forward as known
simplifications, see ``docs/migration/lifecycle-v01-to-v03.md``):

* No ``PendingAuxWrite`` outbox rows fired here — the per-task
  ``Approval(stage=proposed)`` and ``TaskAssignment`` aux materialisation
  is deferred to a follow-on FEAT (FEAT-012's outbox unification covers
  rejection-path Approvals; this expands the same surface to creation /
  approval paths).
* Local ``Task`` rows are inserted inline inside the same dispatch (mirrors
  v0.1.0's ``propose_task``).  Engine 4xx aborts the sequence; previously
  inserted tasks remain visible in the local DB but are not advanced
  further.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from app.core.exceptions import EngineError
from app.modules.ai.executors.base import DispatchContext, ExecutorMode
from app.modules.ai.models import Run, RunMemory, Task, WorkItem
from app.modules.ai.schemas import DispatchEnvelope
from app.modules.ai.tools.lifecycle.memory import (
    LIFECYCLE_MEMORY_NS,
    read_lifecycle_memory,
    write_lifecycle_memory,
)

if TYPE_CHECKING:
    from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient


logger = logging.getLogger(__name__)


class ProposeTasksExecutor:
    """Local executor: T1xN + T2 + T4 per task, then W2 once."""

    mode: ClassVar[ExecutorMode] = "local"

    def __init__(
        self,
        ref: str,
        *,
        task_workflow_id: uuid.UUID,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
        actor: str | None = "lifecycle-agent",
    ) -> None:
        self.name = ref
        self._ref = ref
        self._task_workflow_id = task_workflow_id
        self._client = lifecycle_client
        self._session_factory = session_factory
        self._actor = actor

    async def dispatch(self, ctx: DispatchContext) -> DispatchEnvelope:
        started = datetime.now(UTC)

        memory, work_item_local_id, work_item_engine_id = await self._read_state(ctx.run_id)
        if memory is None:
            return self._failed(
                ctx,
                started=started,
                detail="propose_tasks: RunMemory missing — load_work_item must run first",
            )
        if not memory.tasks:
            return self._failed(
                ctx,
                started=started,
                detail="propose_tasks: LifecycleMemory.tasks is empty — generate_tasks must populate it",
            )
        if work_item_local_id is None or work_item_engine_id is None:
            return self._failed(
                ctx,
                started=started,
                detail=(
                    "propose_tasks: cannot find work_items row for this run — "
                    "register_work_item must run first"
                ),
            )

        # Per-task: create_item(task_workflow) → upsert local row →
        # approve (T2) → assigning (T4) → merge engine_id into memory.
        #
        # BUG-007: the local ``tasks`` row MUST be inserted (and committed)
        # before firing T2/T4 — the engine's webhook for those transitions
        # arrives at the orchestrator's lifecycle reactor, which looks the
        # task up by ``engine_item_id``.  When the row doesn't exist the
        # reactor's status-cache write, effector dispatch, and W2/W5
        # derivations all skip the event silently.  Memory is also patched
        # per-task inside the loop so a later failure preserves earlier
        # tasks' engine ids.
        per_task_engine_ids: dict[str, uuid.UUID] = {}
        for task in memory.tasks:
            external_ref = task.id
            try:
                engine_task_id = await self._client.create_item(
                    workflow_id=self._task_workflow_id,
                    title=task.title,
                    external_ref=external_ref,
                    metadata={
                        "work_item_id": str(work_item_local_id),
                        "executor": task.executor or "",
                    },
                )
            except EngineError as exc:
                return self._failed(
                    ctx,
                    started=started,
                    detail=(
                        f"propose_tasks: engine_error on T1 create_item for {external_ref!r}: {exc}"
                    ),
                )
            except Exception as exc:
                logger.exception(
                    "propose_tasks: T1 create_item raised for task %r", external_ref
                )
                return self._failed(
                    ctx,
                    started=started,
                    detail=f"propose_tasks T1 crashed: {type(exc).__name__}: {exc}",
                )
            per_task_engine_ids[external_ref] = engine_task_id

            # Persist the local row + memory engine_item_id BEFORE the
            # transitions fire, so the reactor's lookup-by-engine_item_id
            # succeeds when the T2/T4 webhooks arrive.
            await self._upsert_local_task(
                work_item_id=work_item_local_id,
                external_ref=external_ref,
                title=task.title,
                engine_task_id=engine_task_id,
            )
            await self._merge_engine_ids_into_memory(
                ctx.run_id, {external_ref: engine_task_id}
            )

            try:
                await self._client.transition_item(
                    item_id=engine_task_id,
                    to_status="approved",
                    correlation_id=uuid.uuid4(),
                    actor=self._actor,
                )
                await self._client.transition_item(
                    item_id=engine_task_id,
                    to_status="assigning",
                    correlation_id=uuid.uuid4(),
                    actor=self._actor,
                )
            except EngineError as exc:
                return self._failed(
                    ctx,
                    started=started,
                    detail=(
                        f"propose_tasks: engine_error advancing {external_ref!r} "
                        f"through T2/T4: {exc}"
                    ),
                )

        # W2 — approve-first-task: open → in_progress on the work item.
        # Idempotent in spirit: if the engine has already advanced (e.g.
        # a partial earlier run), the 422 surfaces as a ``failed`` and an
        # operator can investigate.  The happy path runs once per run.
        try:
            await self._client.transition_item(
                item_id=work_item_engine_id,
                to_status="in_progress",
                correlation_id=uuid.uuid4(),
                actor=self._actor,
            )
        except EngineError as exc:
            return self._failed(
                ctx,
                started=started,
                detail=f"propose_tasks: engine_error firing W2 (open → in_progress): {exc}",
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
                "tasksProposed": len(memory.tasks),
                "engineTaskIds": {k: str(v) for k, v in per_task_engine_ids.items()},
            },
            started_at=started,
            finished_at=datetime.now(UTC),
        )

    async def _read_state(
        self, run_id: uuid.UUID
    ) -> tuple[
        Any | None,  # LifecycleMemory | None — annotation kept loose to avoid module cycle
        uuid.UUID | None,
        uuid.UUID | None,
    ]:
        async with self._session_factory() as session:
            mem_row = await session.scalar(
                select(RunMemory).where(RunMemory.run_id == run_id)
            )
            run_row = await session.get(Run, run_id)
            if mem_row is None or run_row is None:
                return None, None, None
            engine_item_raw = (run_row.intake or {}).get("engineItemId")
            engine_item_id: uuid.UUID | None = None
            local_id: uuid.UUID | None = None
            if engine_item_raw is not None:
                try:
                    engine_item_id = uuid.UUID(str(engine_item_raw))
                except ValueError:
                    engine_item_id = None
                if engine_item_id is not None:
                    wi = await session.scalar(
                        select(WorkItem).where(WorkItem.engine_item_id == engine_item_id)
                    )
                    if wi is not None:
                        local_id = wi.id
        memory = read_lifecycle_memory(mem_row.data or {})
        return memory, local_id, engine_item_id

    async def _upsert_local_task(
        self,
        *,
        work_item_id: uuid.UUID,
        external_ref: str,
        title: str,
        engine_task_id: uuid.UUID,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            existing = await session.scalar(
                select(Task).where(
                    Task.work_item_id == work_item_id,
                    Task.external_ref == external_ref,
                )
            )
            if existing is None:
                session.add(
                    Task(
                        work_item_id=work_item_id,
                        external_ref=external_ref,
                        title=title,
                        status="assigning",
                        proposer_type="agent",
                        proposer_id=self._actor or "lifecycle-agent",
                        engine_item_id=engine_task_id,
                    )
                )
            else:
                if existing.engine_item_id is None:
                    existing.engine_item_id = engine_task_id
                if existing.status != "assigning":
                    existing.status = "assigning"

    async def _merge_engine_ids_into_memory(
        self,
        run_id: uuid.UUID,
        per_task_engine_ids: dict[str, uuid.UUID],
    ) -> None:
        async with self._session_factory() as session, session.begin():
            mem = await session.scalar(
                select(RunMemory).where(RunMemory.run_id == run_id)
            )
            if mem is None:
                return
            existing = dict(mem.data or {})
            ns_data = dict(existing.get(LIFECYCLE_MEMORY_NS) or {})
            tasks_list: list[dict[str, Any]] = list(ns_data.get("tasks") or [])
            for entry in tasks_list:
                tid = str(entry.get("id", ""))
                eid = per_task_engine_ids.get(tid)
                if eid is not None:
                    entry["engineItemId"] = str(eid)
            ns_data["tasks"] = tasks_list
            existing[LIFECYCLE_MEMORY_NS] = ns_data
            mem.data = existing
            flag_modified(mem, "data")

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


_ = write_lifecycle_memory  # kept for symmetry / future use

__all__ = ["ProposeTasksExecutor"]
