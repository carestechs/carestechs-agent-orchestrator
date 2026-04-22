"""FEAT-008/T-169 — status is a reactor-managed cache under engine-present.

Three scenarios:

1. Engine-present signal does NOT mutate ``tasks.status`` inline;
   the cache shows the pre-signal value until the webhook fires.
2. Synthetic matched webhook causes the reactor to write the new
   status onto the cache row.
3. Engine-absent mode keeps the pre-FEAT-008 inline-write path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import ActorRole, TaskStatus, WorkItemStatus
from app.modules.ai.lifecycle import reactor, service
from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient
from app.modules.ai.models import Task, WorkItem

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _build_webhook(
    *,
    item_id: uuid.UUID,
    correlation_id: uuid.UUID | None,
    from_status: str | None,
    to_status: str,
) -> reactor.LifecycleWebhookEvent:
    triggered_by = (
        f"orchestrator-corr:{correlation_id}" if correlation_id else "engine"
    )
    return reactor.LifecycleWebhookEvent(
        delivery_id=uuid.uuid4(),
        event_type="item.transitioned",
        tenant_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        item_id=item_id,
        timestamp=datetime.now(UTC),
        data=reactor.LifecycleWebhookData(
            from_status=from_status,
            to_status=to_status,
            triggered_by=triggered_by,
        ),
    )


async def _seed_task_at_impl_review(db: AsyncSession) -> Task:
    wi = WorkItem(
        external_ref=f"FEAT-{uuid.uuid4().hex[:6]}",
        type="FEAT",
        title="t",
        status=WorkItemStatus.IN_PROGRESS.value,
        opened_by="admin",
    )
    db.add(wi)
    await db.flush()
    task = Task(
        work_item_id=wi.id,
        external_ref=f"T-{uuid.uuid4().hex[:6]}",
        title="t",
        status=TaskStatus.IMPL_REVIEW.value,
        proposer_type="admin",
        proposer_id="admin",
        engine_item_id=uuid.uuid4(),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


def _mock_engine() -> Any:
    mock = AsyncMock(spec=FlowEngineLifecycleClient)
    mock.transition_item = AsyncMock(return_value=None)
    return mock


async def test_engine_present_signal_does_not_mutate_status_inline(
    db_session: AsyncSession,
) -> None:
    task = await _seed_task_at_impl_review(db_session)
    task_id = task.id
    engine = _mock_engine()

    await service.approve_review_signal(
        db_session,
        task_id,
        actor="admin",
        actor_role=ActorRole.ADMIN,
        solo_dev=True,
        engine=cast("Any", engine),
        github=None,
    )

    fresh = await db_session.scalar(select(Task).where(Task.id == task_id))
    assert fresh is not None
    await db_session.refresh(fresh)
    assert fresh.status == TaskStatus.IMPL_REVIEW.value


async def test_reactor_updates_status_cache_on_webhook(
    db_session: AsyncSession,
) -> None:
    task = await _seed_task_at_impl_review(db_session)
    task_id = task.id
    engine_item_id = task.engine_item_id
    assert engine_item_id is not None

    await reactor.handle_transition(
        db_session,
        _build_webhook(
            item_id=engine_item_id,
            correlation_id=None,
            from_status=TaskStatus.IMPL_REVIEW.value,
            to_status=TaskStatus.DONE.value,
        ),
    )
    await db_session.commit()

    fresh = await db_session.scalar(select(Task).where(Task.id == task_id))
    assert fresh is not None
    await db_session.refresh(fresh)
    assert fresh.status == TaskStatus.DONE.value


async def test_engine_absent_fallback_writes_status_inline(
    db_session: AsyncSession,
) -> None:
    task = await _seed_task_at_impl_review(db_session)
    task_id = task.id

    await service.approve_review_signal(
        db_session,
        task_id,
        actor="admin",
        actor_role=ActorRole.ADMIN,
        solo_dev=True,
        engine=None,
        github=None,
    )

    fresh = await db_session.scalar(select(Task).where(Task.id == task_id))
    assert fresh is not None
    await db_session.refresh(fresh)
    assert fresh.status == TaskStatus.DONE.value


async def test_status_cache_miss_is_logged_and_skipped(
    db_session: AsyncSession,
) -> None:
    """Webhook for an engine_item_id the orchestrator doesn't know.

    Defensive guard — the reactor logs and returns rather than raising.
    """
    unknown = uuid.uuid4()
    # Does not raise.
    await reactor.handle_transition(
        db_session,
        _build_webhook(
            item_id=unknown,
            correlation_id=None,
            from_status=None,
            to_status=TaskStatus.DONE.value,
        ),
    )


async def test_work_item_status_cache_updates_on_webhook(
    db_session: AsyncSession,
) -> None:
    wi = WorkItem(
        external_ref=f"FEAT-{uuid.uuid4().hex[:6]}",
        type="FEAT",
        title="t",
        status=WorkItemStatus.IN_PROGRESS.value,
        opened_by="admin",
        engine_item_id=uuid.uuid4(),
    )
    db_session.add(wi)
    await db_session.commit()
    await db_session.refresh(wi)
    assert wi.engine_item_id is not None

    await reactor.handle_transition(
        db_session,
        _build_webhook(
            item_id=wi.engine_item_id,
            correlation_id=None,
            from_status=WorkItemStatus.IN_PROGRESS.value,
            to_status=WorkItemStatus.LOCKED.value,
        ),
    )
    await db_session.commit()

    await db_session.refresh(wi)
    assert wi.status == WorkItemStatus.LOCKED.value
