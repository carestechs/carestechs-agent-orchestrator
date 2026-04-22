"""Request-assignment effector (FEAT-008/T-163).

Fires when a task enters the ``assigning`` state (T4 — triggered by
``approve-task``). V1 transport is log-only: one structured record per
fire with ``task_ref``, ``work_item_ref``, and ``title`` so operators
can grep for pending assignments in the JSON stack. Future Slack /
email / webhook transports land as additional effectors registered on
the same key.
"""

from __future__ import annotations

import logging
import time
from typing import ClassVar

from sqlalchemy import select

from app.modules.ai.lifecycle.effectors.context import (
    EffectorContext,
    EffectorResult,
)
from app.modules.ai.models import Task, WorkItem

logger = logging.getLogger(__name__)


class RequestAssignmentEffector:
    """Emits a structured "task needs assignee" log + trace line."""

    name: ClassVar[str] = "request_assignment"

    async def fire(self, ctx: EffectorContext) -> EffectorResult:
        start = time.monotonic()
        task = await ctx.db.scalar(select(Task).where(Task.id == ctx.entity_id))
        if task is None:
            return EffectorResult(
                effector_name=self.name,
                status="error",
                duration_ms=int((time.monotonic() - start) * 1000),
                error_code="task-not-found",
            )
        wi = await ctx.db.scalar(
            select(WorkItem).where(WorkItem.id == task.work_item_id)
        )
        wi_ref = wi.external_ref if wi is not None else None
        logger.info(
            "task needs assignee",
            extra={
                "task_id": str(task.id),
                "task_ref": task.external_ref,
                "work_item_id": str(task.work_item_id),
                "work_item_ref": wi_ref,
                "title": task.title,
            },
        )
        return EffectorResult(
            effector_name=self.name,
            status="ok",
            duration_ms=int((time.monotonic() - start) * 1000),
            metadata={
                "task_ref": task.external_ref,
                "work_item_ref": wi_ref,
            },
        )
