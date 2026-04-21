"""FEAT-008 / T-167 — reactor materializes aux rows from outbox on webhook.

Covers the four scenarios that define the load-bearing pivot:

1. engine-present signal writes outbox row only; no aux row yet.
2. synthetic matched webhook materializes the aux row and deletes outbox.
3. duplicate webhook is idempotent — the second delivery no-ops.
4. engine-absent fallback preserves inline-write behavior — no outbox row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import ActorRole, TaskStatus, WorkItemStatus
from app.modules.ai.lifecycle import reactor, service
from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient
from app.modules.ai.models import (
    Approval,
    PendingAuxWrite,
    Task,
    TaskImplementation,
    WorkItem,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _build_webhook(
    *, item_id: uuid.UUID, correlation_id: uuid.UUID | None, to_status: str
) -> reactor.LifecycleWebhookEvent:
    triggered_by = (
        f"orchestrator-corr:{correlation_id}"
        if correlation_id is not None
        else "engine"
    )
    return reactor.LifecycleWebhookEvent(
        delivery_id=uuid.uuid4(),
        event_type="item.transitioned",
        tenant_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        item_id=item_id,
        timestamp=datetime.now(UTC),
        data=reactor.LifecycleWebhookData(
            from_status=None, to_status=to_status, triggered_by=triggered_by
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


async def test_engine_present_signal_writes_outbox_not_aux(
    db_session: AsyncSession,
) -> None:
    """S12 approve-review with engine present: Approval deferred to reactor."""
    task = await _seed_task_at_impl_review(db_session)
    task_id = task.id
    engine = _mock_engine()

    returned, is_new = await service.approve_review_signal(
        db_session,
        task_id,
        actor="admin",
        actor_role=ActorRole.ADMIN,
        solo_dev=True,
        engine=cast("Any", engine),
        github=None,
    )
    assert is_new
    assert returned.status == TaskStatus.DONE.value

    approvals = await db_session.scalar(
        select(func.count())
        .select_from(Approval)
        .where(Approval.task_id == task_id)
    )
    pending = await db_session.scalar(
        select(PendingAuxWrite).where(PendingAuxWrite.entity_id == task_id)
    )

    assert approvals == 0, "Approval must be deferred to reactor"
    assert pending is not None
    assert pending.payload["aux_type"] == "approval"
    assert pending.payload["stage"] == "impl"
    assert pending.payload["decision"] == "approve"
    assert pending.signal_name == "approve-review"


async def test_matched_webhook_materializes_aux_and_deletes_outbox(
    db_session: AsyncSession,
) -> None:
    task = await _seed_task_at_impl_review(db_session)
    task_id = task.id
    engine_item_id = task.engine_item_id
    assert engine_item_id is not None

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

    pending = await db_session.scalar(
        select(PendingAuxWrite).where(PendingAuxWrite.entity_id == task_id)
    )
    assert pending is not None
    correlation_id = pending.correlation_id

    await reactor.handle_transition(
        db_session,
        _build_webhook(
            item_id=engine_item_id,
            correlation_id=correlation_id,
            to_status=TaskStatus.DONE.value,
        ),
    )
    await db_session.commit()

    approval = await db_session.scalar(
        select(Approval).where(Approval.task_id == task_id)
    )
    pending_after = await db_session.scalar(
        select(PendingAuxWrite).where(PendingAuxWrite.entity_id == task_id)
    )

    assert approval is not None
    assert approval.stage == "impl"
    assert approval.decision == "approve"
    assert approval.decided_by == "admin"
    assert approval.decided_by_role == ActorRole.ADMIN.value
    assert pending_after is None


async def test_duplicate_webhook_is_idempotent(db_session: AsyncSession) -> None:
    task = await _seed_task_at_impl_review(db_session)
    task_id = task.id
    engine_item_id = task.engine_item_id
    assert engine_item_id is not None

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
    pending = await db_session.scalar(
        select(PendingAuxWrite).where(PendingAuxWrite.entity_id == task_id)
    )
    assert pending is not None
    corr = pending.correlation_id

    webhook = _build_webhook(
        item_id=engine_item_id,
        correlation_id=corr,
        to_status=TaskStatus.DONE.value,
    )
    await reactor.handle_transition(db_session, webhook)
    await db_session.commit()

    # Second delivery: same correlation, no outbox row → no-op, no duplicate.
    await reactor.handle_transition(db_session, webhook)
    await db_session.commit()

    approval_count = await db_session.scalar(
        select(func.count())
        .select_from(Approval)
        .where(Approval.task_id == task_id)
    )
    assert approval_count == 1


async def test_engine_absent_fallback_writes_inline(
    db_session: AsyncSession,
) -> None:
    """Engine-absent mode keeps the pre-FEAT-008 inline-write path."""
    task = await _seed_task_at_impl_review(db_session)
    task_id = task.id

    await service.approve_review_signal(
        db_session,
        task_id,
        actor="admin",
        actor_role=ActorRole.ADMIN,
        solo_dev=True,
        engine=None,  # engine-absent
        github=None,
    )

    approvals = await db_session.scalar(
        select(func.count())
        .select_from(Approval)
        .where(Approval.task_id == task_id)
    )
    pending = await db_session.scalar(
        select(PendingAuxWrite).where(PendingAuxWrite.entity_id == task_id)
    )

    assert approvals == 1
    assert pending is None


async def test_submit_implementation_outbox_carries_pr_url(
    db_session: AsyncSession,
) -> None:
    """Regression guard: TaskImplementation outbox payload round-trips."""
    wi = WorkItem(
        external_ref=f"FEAT-{uuid.uuid4().hex[:6]}",
        type="FEAT",
        title="t",
        status=WorkItemStatus.IN_PROGRESS.value,
        opened_by="admin",
    )
    db_session.add(wi)
    await db_session.flush()
    task = Task(
        work_item_id=wi.id,
        external_ref=f"T-{uuid.uuid4().hex[:6]}",
        title="t",
        status=TaskStatus.IMPLEMENTING.value,
        proposer_type="admin",
        proposer_id="admin",
        engine_item_id=uuid.uuid4(),
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    task_id = task.id
    engine_item_id = task.engine_item_id
    assert engine_item_id is not None

    engine = _mock_engine()
    await service.submit_implementation_signal(
        db_session,
        task_id,
        pr_url="https://github.com/a/b/pull/1",
        commit_sha="deadbeef",
        summary="done",
        actor="dev-1",
        engine=cast("Any", engine),
        github=None,
    )

    pending = await db_session.scalar(
        select(PendingAuxWrite).where(PendingAuxWrite.entity_id == task_id)
    )
    assert pending is not None
    assert pending.payload["aux_type"] == "task_implementation"
    assert pending.payload["pr_url"] == "https://github.com/a/b/pull/1"
    assert pending.payload["commit_sha"] == "deadbeef"

    impls_before = await db_session.scalar(
        select(func.count())
        .select_from(TaskImplementation)
        .where(TaskImplementation.task_id == task_id)
    )
    assert impls_before == 0

    await reactor.handle_transition(
        db_session,
        _build_webhook(
            item_id=engine_item_id,
            correlation_id=pending.correlation_id,
            to_status=TaskStatus.IMPL_REVIEW.value,
        ),
    )
    await db_session.commit()

    impl = await db_session.scalar(
        select(TaskImplementation).where(TaskImplementation.task_id == task_id)
    )
    assert impl is not None
    assert impl.pr_url == "https://github.com/a/b/pull/1"
    assert impl.commit_sha == "deadbeef"
