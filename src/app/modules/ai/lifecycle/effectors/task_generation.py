"""Task-generation effector (FEAT-008/T-164).

Replaces the log-only ``dispatch_task_generation`` stub with a deterministic
seed-task generator. On entry to a freshly-opened work item, emits 1-3
``proposed`` tasks per a type-keyed scaffold so the downstream flow has
something to approve. Scaffolds are placeholders — the real value arrives
when an LLM-backed generator replaces this effector under the same key.

Commit boundary: the effector calls ``ctx.db.commit()`` once after adding
all seed tasks. Consistent with ``_SCAFFOLDS`` below — the caller hands its
session to the effector and expects the effector to own the transaction
for the scaffold write, the same shape as the GitHub effectors.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import ClassVar

from sqlalchemy import func, select

from app.modules.ai.enums import ActorType, TaskStatus, WorkItemType
from app.modules.ai.lifecycle.effectors.context import (
    EffectorContext,
    EffectorResult,
)
from app.modules.ai.models import Task, WorkItem

logger = logging.getLogger(__name__)


# Placeholder scaffolds, keyed by WorkItemType. When an LLM-backed generator
# lands it registers on the same key and these shrink to "fallback for when
# the LLM is unavailable" (or go away entirely).
_SCAFFOLDS: dict[str, list[str]] = {
    WorkItemType.FEAT.value: [
        "Investigate + plan",
        "Implement",
        "Review + close",
    ],
    WorkItemType.BUG.value: [
        "Reproduce + root cause",
        "Fix",
        "Verify + close",
    ],
    WorkItemType.IMP.value: [
        "Scope + plan",
        "Apply improvement",
    ],
}

_PROPOSER_ID = "task_generation"


class GenerateTasksEffector:
    """Seed N proposed tasks for a freshly-opened work item."""

    name: ClassVar[str] = "generate_tasks"

    async def fire(self, ctx: EffectorContext) -> EffectorResult:
        start = time.monotonic()
        wi = await ctx.db.scalar(select(WorkItem).where(WorkItem.id == ctx.entity_id))
        if wi is None:
            return EffectorResult(
                effector_name=self.name,
                status="error",
                duration_ms=int((time.monotonic() - start) * 1000),
                error_code="work-item-not-found",
            )

        existing = await ctx.db.scalar(select(func.count(Task.id)).where(Task.work_item_id == wi.id))
        if existing and existing > 0:
            return EffectorResult(
                effector_name=self.name,
                status="skipped",
                duration_ms=int((time.monotonic() - start) * 1000),
                detail=f"{existing} task(s) already exist",
                metadata={"existing": int(existing)},
            )

        titles = _SCAFFOLDS.get(wi.type, _SCAFFOLDS[WorkItemType.FEAT.value])
        created_refs: list[str] = []
        for idx, title in enumerate(titles, start=1):
            ref = f"T-{wi.external_ref}-{idx:02d}"
            task = Task(
                id=uuid.uuid4(),
                work_item_id=wi.id,
                external_ref=ref,
                title=title,
                status=TaskStatus.PROPOSED.value,
                proposer_type=ActorType.AGENT.value,
                proposer_id=_PROPOSER_ID,
            )
            ctx.db.add(task)
            created_refs.append(ref)
        await ctx.db.commit()

        logger.info(
            "tasks generated",
            extra={
                "work_item_id": str(wi.id),
                "work_item_ref": wi.external_ref,
                "work_item_type": wi.type,
                "task_refs": created_refs,
                "count": len(created_refs),
            },
        )
        return EffectorResult(
            effector_name=self.name,
            status="ok",
            duration_ms=int((time.monotonic() - start) * 1000),
            metadata={
                "task_refs": created_refs,
                "count": len(created_refs),
            },
        )
