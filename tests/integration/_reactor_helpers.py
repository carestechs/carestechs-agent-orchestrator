"""Polling helpers for tests that assert on post-signal reactor output.

T-166 ships these ahead of T-167 (which flips aux-row writes to the
reactor). Today every FEAT-006/007 integration test reads aux rows
synchronously after a 202; once the reactor owns those writes, the row
lands only after the engine's ``item.transitioned`` webhook round-trip.

The helper pattern keeps every assertion as authoritative as a sync
read — the predicate is re-run each poll against a fresh query so stale
ORM state never lies to the caller.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")

_DEFAULT_TIMEOUT_SECONDS = 5.0
_DEFAULT_INTERVAL_SECONDS = 0.05


class ReactorWaitTimeout(AssertionError):
    """Raised when :func:`await_reactor` exhausts its budget without a match."""


async def await_reactor(
    session: AsyncSession,
    predicate: Callable[[AsyncSession], Awaitable[T]],
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    interval: float = _DEFAULT_INTERVAL_SECONDS,
    description: str = "reactor predicate",
) -> T:
    """Poll *predicate* until it returns truthy or *timeout* elapses.

    The predicate runs inside the caller's session. Tests that hold
    stale ORM instances should requery inside the predicate rather than
    expire the session — the session's identity map is not part of the
    contract here, the query is.

    On timeout, raises :class:`ReactorWaitTimeout` with the last (falsy)
    result and *description* so the failure message is self-describing.
    """
    deadline = time.monotonic() + timeout
    last_result: T | None = None
    while True:
        last_result = await predicate(session)
        if last_result:
            return last_result
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(interval)
    raise ReactorWaitTimeout(
        f"{description} did not become truthy within {timeout}s; "
        f"last result: {last_result!r}"
    )


async def await_task_status(
    session: AsyncSession,
    task_id: uuid.UUID,
    expected: str,
    **kwargs: Any,
) -> Any:
    """Poll until task *task_id* reports ``status == expected``."""
    from app.modules.ai.models import Task

    async def predicate(s: AsyncSession) -> Any:
        task = await s.scalar(select(Task).where(Task.id == task_id))
        if task is None:
            return None
        await s.refresh(task)
        return task if task.status == expected else None

    return await await_reactor(
        session,
        predicate,
        description=f"task {task_id} status == {expected}",
        **kwargs,
    )


async def await_work_item_status(
    session: AsyncSession,
    work_item_id: uuid.UUID,
    expected: str,
    **kwargs: Any,
) -> Any:
    """Poll until work item *work_item_id* reports ``status == expected``."""
    from app.modules.ai.models import WorkItem

    async def predicate(s: AsyncSession) -> Any:
        wi = await s.scalar(select(WorkItem).where(WorkItem.id == work_item_id))
        if wi is None:
            return None
        await s.refresh(wi)
        return wi if wi.status == expected else None

    return await await_reactor(
        session,
        predicate,
        description=f"work_item {work_item_id} status == {expected}",
        **kwargs,
    )


async def await_aux_row_count(
    session: AsyncSession,
    model: type,
    *,
    task_id: uuid.UUID | None = None,
    work_item_id: uuid.UUID | None = None,
    minimum: int = 1,
    **kwargs: Any,
) -> int:
    """Poll until *model* has at least *minimum* rows for the given parent."""
    if task_id is None and work_item_id is None:
        raise ValueError("await_aux_row_count requires task_id or work_item_id")

    async def predicate(s: AsyncSession) -> int:
        stmt = select(func.count()).select_from(model)
        if task_id is not None:
            stmt = stmt.where(model.task_id == task_id)  # type: ignore[attr-defined]
        else:
            stmt = stmt.where(
                model.work_item_id == work_item_id  # type: ignore[attr-defined]
            )
        count = await s.scalar(stmt)
        return count if count and count >= minimum else 0

    parent = f"task {task_id}" if task_id is not None else f"work_item {work_item_id}"
    return await await_reactor(
        session,
        predicate,
        description=f"{model.__name__} count >= {minimum} for {parent}",
        **kwargs,
    )
