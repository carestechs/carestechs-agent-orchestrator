"""Unit tests for the outbox reconciliation loop (FEAT-008/T-170)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import TaskStatus, WorkItemStatus
from app.modules.ai.lifecycle.reconciliation import (
    ReconciliationReport,
    format_report,
    reconcile,
)
from app.modules.ai.models import (
    Approval,
    PendingAuxWrite,
    Task,
    TaskAssignment,
    WorkItem,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")


# ---------------------------------------------------------------------------
# Stub engine client — shape matches FlowEngineLifecycleClient.get_item_state
# ---------------------------------------------------------------------------


@dataclass
class _StubEngine:
    states: dict[uuid.UUID, str | None]
    raise_on: set[uuid.UUID] | None = None

    async def get_item_state(self, item_id: uuid.UUID) -> str | None:
        if self.raise_on and item_id in self.raise_on:
            raise RuntimeError(f"simulated engine failure for {item_id}")
        return self.states.get(item_id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _seed_task_with_engine_id(db: AsyncSession) -> Task:
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
        title="implement feature",
        status=TaskStatus.APPROVED.value,
        proposer_type="admin",
        proposer_id="admin",
        engine_item_id=uuid.uuid4(),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def _enqueue_pending_approval(
    db: AsyncSession,
    task: Task,
    *,
    signal_name: str = "approve-task",
) -> PendingAuxWrite:
    pending = PendingAuxWrite(
        correlation_id=uuid.uuid4(),
        signal_name=signal_name,
        entity_type="task",
        entity_id=task.id,
        payload={
            "aux_type": "approval",
            "stage": "proposed",
            "decision": "approve",
            "decided_by": "admin",
            "decided_by_role": "admin",
            "feedback": None,
        },
    )
    db.add(pending)
    await db.commit()
    await db.refresh(pending)
    return pending


async def _count(
    db: AsyncSession, model: type, predicate: Callable[[], Awaitable[object]] | None = None
) -> int:
    del predicate
    raw = await db.scalar(select(func.count()).select_from(model))
    return int(raw or 0)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_materializes_when_engine_confirms_target_state(
    db_session: AsyncSession,
) -> None:
    task = await _seed_task_with_engine_id(db_session)
    pending = await _enqueue_pending_approval(db_session, task)
    assert task.engine_item_id is not None
    engine = _StubEngine(states={task.engine_item_id: "assigning"})

    report = await reconcile(db_session, engine)  # type: ignore[arg-type]

    assert report.scanned == 1
    assert report.materialized == 1
    assert report.skipped_stale == 0
    assert report.skipped_unknown == 0
    # Pending row drained:
    drained = await db_session.scalar(
        select(PendingAuxWrite).where(
            PendingAuxWrite.correlation_id == pending.correlation_id
        )
    )
    assert drained is None
    # Approval row landed:
    approvals = list(
        await db_session.scalars(select(Approval).where(Approval.task_id == task.id))
    )
    assert len(approvals) == 1
    assert approvals[0].decision == "approve"


async def test_rejection_signal_materializes_unconditionally(
    db_session: AsyncSession,
) -> None:
    """Rejections don't advance engine state; materialize regardless."""
    task = await _seed_task_with_engine_id(db_session)
    pending = await _enqueue_pending_approval(
        db_session, task, signal_name="reject-task"
    )
    assert task.engine_item_id is not None
    # Engine state still at approved; reconcile should materialize anyway.
    engine = _StubEngine(states={task.engine_item_id: "approved"})

    report = await reconcile(db_session, engine)  # type: ignore[arg-type]

    assert report.materialized == 1
    drained = await db_session.scalar(
        select(PendingAuxWrite).where(
            PendingAuxWrite.correlation_id == pending.correlation_id
        )
    )
    assert drained is None


# ---------------------------------------------------------------------------
# Preserved buckets
# ---------------------------------------------------------------------------


async def test_preserves_pending_when_engine_state_mismatches(
    db_session: AsyncSession,
) -> None:
    task = await _seed_task_with_engine_id(db_session)
    pending = await _enqueue_pending_approval(db_session, task)
    assert task.engine_item_id is not None
    engine = _StubEngine(states={task.engine_item_id: "approved"})  # not assigning

    report = await reconcile(db_session, engine)  # type: ignore[arg-type]

    assert report.skipped_stale == 1
    assert report.materialized == 0
    # Pending row preserved:
    still = await db_session.scalar(
        select(PendingAuxWrite).where(
            PendingAuxWrite.correlation_id == pending.correlation_id
        )
    )
    assert still is not None


async def test_skipped_unknown_when_engine_returns_none(
    db_session: AsyncSession,
) -> None:
    task = await _seed_task_with_engine_id(db_session)
    await _enqueue_pending_approval(db_session, task)
    engine = _StubEngine(states={})  # engine doesn't know the item

    report = await reconcile(db_session, engine)  # type: ignore[arg-type]

    assert report.skipped_unknown == 1
    assert report.materialized == 0


async def test_skipped_unknown_when_entity_missing_locally(
    db_session: AsyncSession,
) -> None:
    """Outbox row for a task the orchestrator doesn't have a row for."""
    pending = PendingAuxWrite(
        correlation_id=uuid.uuid4(),
        signal_name="approve-task",
        entity_type="task",
        entity_id=uuid.uuid4(),  # no matching Task
        payload={
            "aux_type": "approval",
            "stage": "proposed",
            "decision": "approve",
            "decided_by": "admin",
            "decided_by_role": "admin",
        },
    )
    db_session.add(pending)
    await db_session.commit()

    report = await reconcile(db_session, _StubEngine(states={}))  # type: ignore[arg-type]

    assert report.skipped_unknown == 1


# ---------------------------------------------------------------------------
# Dry run + idempotency + error isolation
# ---------------------------------------------------------------------------


async def test_dry_run_does_not_commit_changes(db_session: AsyncSession) -> None:
    task = await _seed_task_with_engine_id(db_session)
    pending = await _enqueue_pending_approval(db_session, task)
    pending_id = pending.correlation_id
    task_id = task.id
    assert task.engine_item_id is not None
    engine = _StubEngine(states={task.engine_item_id: "assigning"})

    report = await reconcile(db_session, engine, dry_run=True)  # type: ignore[arg-type]

    # Report reflects what *would* happen:
    assert report.materialized == 1
    # ... but the pending row still exists and no Approval was committed.
    still = await db_session.scalar(
        select(PendingAuxWrite).where(PendingAuxWrite.correlation_id == pending_id)
    )
    assert still is not None
    approvals_count = await db_session.scalar(
        select(func.count()).select_from(Approval).where(Approval.task_id == task_id)
    )
    assert (approvals_count or 0) == 0


async def test_idempotent_second_run_finds_nothing(
    db_session: AsyncSession,
) -> None:
    task = await _seed_task_with_engine_id(db_session)
    await _enqueue_pending_approval(db_session, task)
    assert task.engine_item_id is not None
    engine = _StubEngine(states={task.engine_item_id: "assigning"})

    first = await reconcile(db_session, engine)  # type: ignore[arg-type]
    second = await reconcile(db_session, engine)  # type: ignore[arg-type]

    assert first.materialized == 1
    assert second.scanned == 0
    assert second.materialized == 0


async def test_exception_on_one_row_isolates_others(
    db_session: AsyncSession,
) -> None:
    good = await _seed_task_with_engine_id(db_session)
    bad = await _seed_task_with_engine_id(db_session)
    await _enqueue_pending_approval(db_session, good)
    await _enqueue_pending_approval(db_session, bad)
    assert good.engine_item_id is not None
    assert bad.engine_item_id is not None
    engine = _StubEngine(
        states={good.engine_item_id: "assigning", bad.engine_item_id: "assigning"},
        raise_on={bad.engine_item_id},
    )

    report = await reconcile(db_session, engine)  # type: ignore[arg-type]

    assert report.scanned == 2
    assert report.materialized == 1
    assert len(report.errors) == 1


# ---------------------------------------------------------------------------
# Since-window filter
# ---------------------------------------------------------------------------


async def test_since_window_excludes_old_rows(db_session: AsyncSession) -> None:
    task = await _seed_task_with_engine_id(db_session)
    # Seed two pending rows; backdate one beyond the window.
    old = await _enqueue_pending_approval(db_session, task)
    recent = await _enqueue_pending_approval(
        db_session, task, signal_name="reject-task"
    )
    old.enqueued_at = datetime.now(UTC) - timedelta(days=10)
    await db_session.commit()
    assert task.engine_item_id is not None

    engine = _StubEngine(states={task.engine_item_id: "assigning"})
    report = await reconcile(db_session, engine, since=timedelta(hours=1))  # type: ignore[arg-type]

    # Only the recent row was scanned (reject-task materializes unconditionally).
    assert report.scanned == 1
    assert report.materialized == 1
    # Old row preserved:
    kept = await db_session.scalar(
        select(PendingAuxWrite).where(
            PendingAuxWrite.correlation_id == old.correlation_id
        )
    )
    assert kept is not None
    del recent


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


def test_format_report_shape() -> None:
    report = ReconciliationReport(
        scanned=5,
        materialized=2,
        skipped_stale=1,
        skipped_unknown=1,
        errors=["abc: boom"],
    )
    out = format_report(report, dry_run=True)
    assert "dry-run: true" in out
    assert "Scanned:           5" in out
    assert "Materialized:      2" in out
    assert "Skipped (stale):   1" in out
    assert "Skipped (unknown): 1" in out
    assert "Errors:            1" in out
    assert "! abc: boom" in out


# ---------------------------------------------------------------------------
# Aux-type coverage (task_assignment builds the right row)
# ---------------------------------------------------------------------------


async def test_materializes_task_assignment_aux_row(
    db_session: AsyncSession,
) -> None:
    task = await _seed_task_with_engine_id(db_session)
    pending = PendingAuxWrite(
        correlation_id=uuid.uuid4(),
        signal_name="assign-task",
        entity_type="task",
        entity_id=task.id,
        payload={
            "aux_type": "task_assignment",
            "assignee_type": "dev",
            "assignee_id": "dev-1",
            "assigned_by": "admin",
        },
    )
    db_session.add(pending)
    await db_session.commit()
    assert task.engine_item_id is not None
    engine = _StubEngine(states={task.engine_item_id: "planning"})

    report = await reconcile(db_session, engine)  # type: ignore[arg-type]

    assert report.materialized == 1
    assignments = list(
        await db_session.scalars(
            select(TaskAssignment).where(TaskAssignment.task_id == task.id)
        )
    )
    assert len(assignments) == 1
    assert assignments[0].assignee_id == "dev-1"
