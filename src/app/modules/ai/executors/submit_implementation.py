"""Idempotent T9 ``implementing → impl_review`` executor (BUG-004).

The lifecycle's review-correction loop walks ``request_implementation``
twice (or more) in the failure case: human signal → review → reject →
correction → human signal → review → ... The engine's task workflow
defines T9 (``implementing → impl_review``) as a one-shot edge from
``implementing``; firing it a second time would 422.  Per FEAT-008,
rejection (T11) is **not** mirrored to the engine — the engine task
stays at ``impl_review`` after the first T9, then the orchestrator
re-judges via ``review_implementation`` and either fires T10 (pass) or
records another rejection (fail).

This executor encodes that contract:

* Read the current task from :class:`LifecycleMemory`.
* If ``LifecycleTask.submitted`` is already true → return a ``completed``
  local-mode envelope with no engine call.
* Otherwise fire one ``transition_item(task, "impl_review")`` and flip
  the memory flag inside the ``__memory_patch`` so the next visit is a
  no-op.

Mode is ``"local"`` — the call is synchronous; we do not need the
wake-on-correlation pipeline because the engine validates the
transition in-band and there is no aux row to materialise here.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import EngineError
from app.modules.ai.executors.base import DispatchContext, ExecutorMode
from app.modules.ai.models import RunMemory
from app.modules.ai.schemas import DispatchEnvelope
from app.modules.ai.tools.lifecycle.memory import (
    find_current_task,
    read_lifecycle_memory,
)

if TYPE_CHECKING:
    from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient


logger = logging.getLogger(__name__)


class SubmitImplementationExecutor:
    """Local executor: T9 once per task, no-op on repeat visits."""

    mode: ClassVar[ExecutorMode] = "local"

    def __init__(
        self,
        ref: str,
        *,
        lifecycle_client: FlowEngineLifecycleClient,
        session_factory: async_sessionmaker[AsyncSession],
        actor: str | None = "lifecycle-agent",
    ) -> None:
        self.name = ref
        self._ref = ref
        self._client = lifecycle_client
        self._session_factory = session_factory
        self._actor = actor

    async def dispatch(self, ctx: DispatchContext) -> DispatchEnvelope:
        started = datetime.now(UTC)

        async with self._session_factory() as session:
            mem_row = await session.scalar(
                select(RunMemory).where(RunMemory.run_id == ctx.run_id)
            )
        memory = read_lifecycle_memory((mem_row.data if mem_row is not None else {}) or {})
        task = find_current_task(memory)
        if task is None:
            return self._failed(
                ctx,
                started=started,
                detail=(
                    "submit_implementation: no current task in memory "
                    f"(current_task_id={memory.current_task_id!r})"
                ),
            )
        if task.engine_item_id is None:
            return self._failed(
                ctx,
                started=started,
                detail=f"submit_implementation: task {task.id!r} missing engine_item_id",
            )
        if task.submitted:
            # Idempotent: skip the engine call on repeat visits (the
            # engine task is already at impl_review).
            return self._ok(
                ctx,
                started=started,
                result={"taskId": task.id, "skipped": True},
            )

        try:
            engine_task_id = uuid.UUID(task.engine_item_id)
            await self._client.transition_item(
                item_id=engine_task_id,
                to_status="impl_review",
                correlation_id=uuid.uuid4(),
                actor=self._actor,
            )
        except EngineError as exc:
            return self._failed(
                ctx,
                started=started,
                detail=f"submit_implementation: engine_error firing T9: {exc}",
            )
        except Exception as exc:
            logger.exception(
                "submit_implementation: T9 raised for task %r", task.id
            )
            return self._failed(
                ctx,
                started=started,
                detail=f"submit_implementation: T9 crashed: {type(exc).__name__}: {exc}",
            )

        # Flip the submitted flag via __memory_patch so the runtime's
        # standard merge writes it into RunMemory.data.  Patch is
        # shallow over the lifecycle.v1 namespace; preserve other tasks.
        from app.modules.ai.tools.lifecycle.memory import LIFECYCLE_MEMORY_NS

        ns_data: dict[str, Any] = dict((mem_row.data if mem_row is not None else {}) or {}).get(
            LIFECYCLE_MEMORY_NS
        ) or {}
        ns_data = dict(ns_data)
        tasks_list: list[dict[str, Any]] = list(ns_data.get("tasks") or [])
        for entry in tasks_list:
            if str(entry.get("id", "")) == task.id:
                entry["submitted"] = True
        ns_data["tasks"] = tasks_list
        return self._ok(
            ctx,
            started=started,
            result={
                "taskId": task.id,
                "skipped": False,
                "__memory_patch": {LIFECYCLE_MEMORY_NS: ns_data},
            },
        )

    def _ok(
        self,
        ctx: DispatchContext,
        *,
        started: datetime,
        result: dict[str, Any],
    ) -> DispatchEnvelope:
        return DispatchEnvelope(
            dispatch_id=ctx.dispatch_id,
            step_id=ctx.step_id,
            run_id=ctx.run_id,
            executor_ref=self._ref,
            mode="local",  # type: ignore[arg-type]
            state="completed",  # type: ignore[arg-type]
            intake=dict(ctx.intake),
            outcome="ok",  # type: ignore[arg-type]
            result=result,
            started_at=started,
            finished_at=datetime.now(UTC),
        )

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


__all__ = ["SubmitImplementationExecutor"]
