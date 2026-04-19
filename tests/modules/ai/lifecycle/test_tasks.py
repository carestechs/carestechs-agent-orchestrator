"""Tests for task state-machine transitions (FEAT-006 / T-113)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ValidationError
from app.modules.ai.enums import (
    ActorRole,
    ActorType,
    ApprovalDecision,
    AssigneeType,
    TaskStatus,
    WorkItemType,
)
from app.modules.ai.lifecycle import tasks as task_svc
from app.modules.ai.lifecycle import work_items as wi_svc
from app.modules.ai.models import Approval, Task, TaskAssignment, WorkItem


async def _wi(db: AsyncSession, ref: str = "FEAT-001") -> WorkItem:
    wi = await wi_svc.open_work_item(
        db,
        external_ref=ref,
        type=WorkItemType.FEAT,
        title="t",
        source_path=None,
        opened_by="admin",
    )
    await db.commit()
    return wi


async def _task(db: AsyncSession, wi_id: uuid.UUID, ref: str = "T-001") -> Task:
    t = await task_svc.propose_task(
        db,
        work_item_id=wi_id,
        external_ref=ref,
        title="do thing",
        proposer_type=ActorType.ADMIN,
        proposer_id="admin",
    )
    await db.commit()
    return t


async def _approvals(db: AsyncSession, task_id: uuid.UUID) -> list[Approval]:
    result = await db.scalars(
        select(Approval).where(Approval.task_id == task_id)
    )
    return list(result.all())


# ---------------------------------------------------------------------------
# T1 + T2 + T4
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_and_approve_fires_t4(db_session: AsyncSession) -> None:
    wi = await _wi(db_session)
    t = await _task(db_session, wi.id)
    assert t.status == TaskStatus.PROPOSED.value

    await task_svc.approve_task(db_session, t.id, actor="admin")
    await db_session.commit()
    await db_session.refresh(t)
    assert t.status == TaskStatus.ASSIGNING.value  # T4 fired

    approvals = await _approvals(db_session, t.id)
    assert len(approvals) == 1
    assert approvals[0].decision == ApprovalDecision.APPROVE.value


# ---------------------------------------------------------------------------
# T3 — reject proposal preserves status, requires feedback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_task_proposal_requires_feedback(db_session: AsyncSession) -> None:
    wi = await _wi(db_session)
    t = await _task(db_session, wi.id)
    with pytest.raises(ValidationError):
        await task_svc.reject_task_proposal(
            db_session, t.id, actor="admin", feedback=""
        )


@pytest.mark.asyncio
async def test_reject_loop_three_iterations(db_session: AsyncSession) -> None:
    wi = await _wi(db_session)
    t = await _task(db_session, wi.id)
    for i in range(3):
        await task_svc.reject_task_proposal(
            db_session, t.id, actor="admin", feedback=f"fix {i}"
        )
        await db_session.commit()
    await db_session.refresh(t)
    assert t.status == TaskStatus.PROPOSED.value
    approvals = await _approvals(db_session, t.id)
    assert len(approvals) == 3
    assert all(a.decision == ApprovalDecision.REJECT.value for a in approvals)


# ---------------------------------------------------------------------------
# T5 — assign + reassign supersedes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_from_assigning_then_reassign(db_session: AsyncSession) -> None:
    wi = await _wi(db_session)
    t = await _task(db_session, wi.id)
    await task_svc.approve_task(db_session, t.id, actor="admin")
    await db_session.commit()

    _, a1 = await task_svc.assign_task(
        db_session,
        t.id,
        assignee_type=AssigneeType.DEV,
        assignee_id="dev-1",
        assigned_by="admin",
    )
    await db_session.commit()
    await db_session.refresh(t)
    assert t.status == TaskStatus.PLANNING.value

    # Reassign — manual, but simulate by forcing state back to assigning and
    # calling assign_task again.  T-117+ will handle re-assignment flow more
    # carefully; here we assert the supersede mechanics.
    t.status = TaskStatus.ASSIGNING.value
    await db_session.commit()
    _, a2 = await task_svc.assign_task(
        db_session,
        t.id,
        assignee_type=AssigneeType.AGENT,
        assignee_id="agent-x",
        assigned_by="admin",
    )
    await db_session.commit()

    all_assignments = await db_session.scalars(
        select(TaskAssignment).where(TaskAssignment.task_id == t.id)
    )
    rows = list(all_assignments.all())
    assert len(rows) == 2
    active = [r for r in rows if r.superseded_at is None]
    assert len(active) == 1
    assert active[0].id == a2.id
    superseded = [r for r in rows if r.superseded_at is not None]
    assert len(superseded) == 1
    assert superseded[0].id == a1.id


@pytest.mark.asyncio
async def test_assign_from_wrong_state(db_session: AsyncSession) -> None:
    wi = await _wi(db_session)
    t = await _task(db_session, wi.id)  # proposed
    with pytest.raises(ConflictError):
        await task_svc.assign_task(
            db_session,
            t.id,
            assignee_type=AssigneeType.DEV,
            assignee_id="dev-1",
            assigned_by="admin",
        )


# ---------------------------------------------------------------------------
# T6 / T7 / T8 — plan flow + matrix enforcement
# ---------------------------------------------------------------------------


async def _through_planning(db: AsyncSession, wi: WorkItem, assignee_type: AssigneeType) -> Task:
    t = await _task(db, wi.id, ref=f"T-{assignee_type.value}")
    await task_svc.approve_task(db, t.id, actor="admin")
    await db.commit()
    await task_svc.assign_task(
        db,
        t.id,
        assignee_type=assignee_type,
        assignee_id=f"{assignee_type.value}-1",
        assigned_by="admin",
    )
    await db.commit()
    await task_svc.submit_plan(db, t.id, submitted_by="author")
    await db.commit()
    return t


@pytest.mark.asyncio
async def test_approve_plan_dev_assigned_requires_dev_role(db_session: AsyncSession) -> None:
    wi = await _wi(db_session)
    t = await _through_planning(db_session, wi, AssigneeType.DEV)

    # wrong role
    with pytest.raises(ConflictError):
        await task_svc.approve_plan(
            db_session, t.id, actor="admin", actor_role=ActorRole.ADMIN, solo_dev=True
        )

    await task_svc.approve_plan(
        db_session, t.id, actor="dev-1", actor_role=ActorRole.DEV, solo_dev=True
    )
    await db_session.commit()
    await db_session.refresh(t)
    assert t.status == TaskStatus.IMPLEMENTING.value


@pytest.mark.asyncio
async def test_approve_plan_agent_assigned_requires_admin_role(db_session: AsyncSession) -> None:
    wi = await _wi(db_session)
    t = await _through_planning(db_session, wi, AssigneeType.AGENT)

    with pytest.raises(ConflictError):
        await task_svc.approve_plan(
            db_session, t.id, actor="dev-1", actor_role=ActorRole.DEV, solo_dev=True
        )

    await task_svc.approve_plan(
        db_session, t.id, actor="admin", actor_role=ActorRole.ADMIN, solo_dev=True
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_reject_plan_requires_feedback(db_session: AsyncSession) -> None:
    wi = await _wi(db_session)
    t = await _through_planning(db_session, wi, AssigneeType.DEV)
    with pytest.raises(ValidationError):
        await task_svc.reject_plan(
            db_session,
            t.id,
            actor="dev-1",
            actor_role=ActorRole.DEV,
            feedback="",
            solo_dev=True,
        )


@pytest.mark.asyncio
async def test_reject_plan_returns_to_planning(db_session: AsyncSession) -> None:
    wi = await _wi(db_session)
    t = await _through_planning(db_session, wi, AssigneeType.DEV)
    await task_svc.reject_plan(
        db_session,
        t.id,
        actor="dev-1",
        actor_role=ActorRole.DEV,
        feedback="split it",
        solo_dev=True,
    )
    await db_session.commit()
    await db_session.refresh(t)
    assert t.status == TaskStatus.PLANNING.value


# ---------------------------------------------------------------------------
# T9 / T10 / T11 — impl review
# ---------------------------------------------------------------------------


async def _through_implementing(db: AsyncSession, wi: WorkItem) -> Task:
    t = await _through_planning(db, wi, AssigneeType.DEV)
    await task_svc.approve_plan(
        db, t.id, actor="dev-1", actor_role=ActorRole.DEV, solo_dev=True
    )
    await db.commit()
    await task_svc.submit_implementation(db, t.id, submitted_by="dev-1")
    await db.commit()
    return t


@pytest.mark.asyncio
async def test_approve_review_solo_dev_needs_admin(db_session: AsyncSession) -> None:
    wi = await _wi(db_session)
    t = await _through_implementing(db_session, wi)

    with pytest.raises(ConflictError):
        await task_svc.approve_review(
            db_session, t.id, actor="dev-1", actor_role=ActorRole.DEV, solo_dev=True
        )

    await task_svc.approve_review(
        db_session, t.id, actor="admin", actor_role=ActorRole.ADMIN, solo_dev=True
    )
    await db_session.commit()
    await db_session.refresh(t)
    assert t.status == TaskStatus.DONE.value


@pytest.mark.asyncio
async def test_reject_review_loops_back_to_implementing(db_session: AsyncSession) -> None:
    wi = await _wi(db_session)
    t = await _through_implementing(db_session, wi)

    for i in range(3):
        await task_svc.reject_review(
            db_session,
            t.id,
            actor="admin",
            actor_role=ActorRole.ADMIN,
            feedback=f"round {i}",
            solo_dev=True,
        )
        await db_session.commit()
        await db_session.refresh(t)
        assert t.status == TaskStatus.IMPLEMENTING.value
        await task_svc.submit_implementation(db_session, t.id, submitted_by="dev-1")
        await db_session.commit()

    rejects = await _approvals(db_session, t.id)
    rejects = [a for a in rejects if a.decision == ApprovalDecision.REJECT.value and a.stage == "impl"]
    assert len(rejects) == 3


# ---------------------------------------------------------------------------
# T12 — defer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pre_status",
    [
        TaskStatus.PROPOSED,
        TaskStatus.APPROVED,
        TaskStatus.ASSIGNING,
        TaskStatus.PLANNING,
        TaskStatus.PLAN_REVIEW,
        TaskStatus.IMPLEMENTING,
        TaskStatus.IMPL_REVIEW,
    ],
)
async def test_defer_from_each_non_terminal(
    db_session: AsyncSession, pre_status: TaskStatus
) -> None:
    wi = await _wi(db_session)
    t = await _task(db_session, wi.id, ref=f"T-{pre_status.value}")
    t.status = pre_status.value
    await db_session.commit()

    await task_svc.defer_task(db_session, t.id, actor="admin", reason="x")
    await db_session.commit()
    await db_session.refresh(t)
    assert t.status == TaskStatus.DEFERRED.value
    assert t.deferred_from == pre_status.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal", [TaskStatus.DONE, TaskStatus.DEFERRED]
)
async def test_defer_from_terminal_forbidden(
    db_session: AsyncSession, terminal: TaskStatus
) -> None:
    wi = await _wi(db_session)
    t = await _task(db_session, wi.id, ref=f"T-{terminal.value}")
    t.status = terminal.value
    await db_session.commit()

    with pytest.raises(ConflictError):
        await task_svc.defer_task(db_session, t.id, actor="admin", reason="x")
