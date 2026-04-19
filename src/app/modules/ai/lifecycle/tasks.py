"""Task state-machine transitions (FEAT-006).

Implements transitions T1-T12 from the design doc.  Rejection edges (T3,
T8, T11) preserve owner; feedback required.  Deferral is allowed from any
non-terminal state and fires W5 via the caller.  Every transition holds a
``SELECT ... FOR UPDATE`` on the task row to serialize concurrent writes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.modules.ai.enums import (
    ActorRole,
    ActorType,
    ApprovalDecision,
    ApprovalStage,
    AssigneeType,
    TaskStatus,
)
from app.modules.ai.lifecycle.approval_matrix import approval_matrix
from app.modules.ai.models import Approval, Task, TaskAssignment

# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_TERMINAL = {TaskStatus.DONE.value, TaskStatus.DEFERRED.value}


async def _load_locked(db: AsyncSession, task_id: uuid.UUID) -> Task:
    row = await db.scalar(
        select(Task).where(Task.id == task_id).with_for_update()
    )
    if row is None:
        raise NotFoundError(f"task not found: {task_id}")
    return row


async def _active_assignment(
    db: AsyncSession, task_id: uuid.UUID
) -> TaskAssignment | None:
    return await db.scalar(
        select(TaskAssignment).where(
            TaskAssignment.task_id == task_id,
            TaskAssignment.superseded_at.is_(None),
        )
    )


def _forbidden(task: Task, target: str) -> ConflictError:
    return ConflictError(
        f"task {task.id} cannot transition from {task.status} to {target}"
    )


def _require_feedback(feedback: str | None) -> str:
    if feedback is None or not feedback.strip():
        raise ValidationError("feedback is required on rejection and cannot be empty")
    return feedback


def _record_approval(
    db: AsyncSession,
    *,
    task_id: uuid.UUID,
    stage: ApprovalStage,
    decision: ApprovalDecision,
    decided_by: str,
    decided_by_role: ActorRole,
    feedback: str | None,
) -> Approval:
    row = Approval(
        task_id=task_id,
        stage=stage.value,
        decision=decision.value,
        decided_by=decided_by,
        decided_by_role=decided_by_role.value,
        feedback=feedback,
    )
    db.add(row)
    return row


# ---------------------------------------------------------------------------
# T1 — propose
# ---------------------------------------------------------------------------


async def propose_task(
    db: AsyncSession,
    *,
    work_item_id: uuid.UUID,
    external_ref: str,
    title: str,
    proposer_type: ActorType,
    proposer_id: str,
) -> Task:
    """T1: create a new task in the ``proposed`` state."""
    task = Task(
        work_item_id=work_item_id,
        external_ref=external_ref,
        title=title,
        status=TaskStatus.PROPOSED.value,
        proposer_type=proposer_type.value,
        proposer_id=proposer_id,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# T2 + T4 — approve (advances through assigning automatically)
# ---------------------------------------------------------------------------


async def approve_task(
    db: AsyncSession, task_id: uuid.UUID, *, actor: str
) -> Task:
    """T2+T4: ``proposed -> approved`` then immediately ``approved -> assigning``.

    Writes an ``Approval(stage=proposed, decision=approve)`` row.  Caller is
    expected to fire :func:`work_items.maybe_advance_to_in_progress` after
    this returns.
    """
    task = await _load_locked(db, task_id)
    if task.status != TaskStatus.PROPOSED.value:
        raise _forbidden(task, TaskStatus.APPROVED.value)
    _record_approval(
        db,
        task_id=task_id,
        stage=ApprovalStage.PROPOSED,
        decision=ApprovalDecision.APPROVE,
        decided_by=actor,
        decided_by_role=ActorRole.ADMIN,
        feedback=None,
    )
    # T4 is inline: approved -> assigning without a separate hop.
    task.status = TaskStatus.ASSIGNING.value
    await db.flush()
    await db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# T3 — reject proposal
# ---------------------------------------------------------------------------


async def reject_task_proposal(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    actor: str,
    feedback: str,
) -> Task:
    """T3: ``proposed -> proposed`` (rejection with feedback, same owner)."""
    _require_feedback(feedback)
    task = await _load_locked(db, task_id)
    if task.status != TaskStatus.PROPOSED.value:
        raise _forbidden(task, "proposed (reject)")
    _record_approval(
        db,
        task_id=task_id,
        stage=ApprovalStage.PROPOSED,
        decision=ApprovalDecision.REJECT,
        decided_by=actor,
        decided_by_role=ActorRole.ADMIN,
        feedback=feedback,
    )
    # status unchanged; updated_at bumps via onupdate
    task.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# T5 — assign (admin writes assignee)
# ---------------------------------------------------------------------------


async def assign_task(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    assignee_type: AssigneeType,
    assignee_id: str,
    assigned_by: str,
) -> tuple[Task, TaskAssignment]:
    """T5: ``assigning -> planning``.  Inserts a new TaskAssignment row.

    If an active assignment already exists (reassignment), it is superseded
    in the same transaction (partial-unique index is preserved).
    """
    task = await _load_locked(db, task_id)
    if task.status != TaskStatus.ASSIGNING.value:
        raise _forbidden(task, TaskStatus.PLANNING.value)

    prior = await _active_assignment(db, task_id)
    if prior is not None:
        prior.superseded_at = datetime.now(UTC)
        await db.flush()

    assignment = TaskAssignment(
        task_id=task_id,
        assignee_type=assignee_type.value,
        assignee_id=assignee_id,
        assigned_by=assigned_by,
    )
    db.add(assignment)
    task.status = TaskStatus.PLANNING.value
    await db.flush()
    await db.refresh(task)
    await db.refresh(assignment)
    return task, assignment


# ---------------------------------------------------------------------------
# T6 — submit plan
# ---------------------------------------------------------------------------


async def submit_plan(
    db: AsyncSession, task_id: uuid.UUID, *, submitted_by: str
) -> Task:
    """T6: ``planning -> plan_review``.

    Plan-row persistence (``TaskPlan``) lands in T-117; this function only
    advances the task state.
    """
    del submitted_by  # audited by caller (T-117)
    task = await _load_locked(db, task_id)
    if task.status != TaskStatus.PLANNING.value:
        raise _forbidden(task, TaskStatus.PLAN_REVIEW.value)
    task.status = TaskStatus.PLAN_REVIEW.value
    await db.flush()
    await db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# T7 / T8 — plan approve / reject
# ---------------------------------------------------------------------------


async def _matrix_or_forbidden(
    db: AsyncSession,
    task: Task,
    stage: ApprovalStage,
    actor_role: ActorRole,
    *,
    solo_dev: bool,
) -> None:
    assignment = await _active_assignment(db, task.id)
    required = approval_matrix(task, assignment, stage, solo_dev=solo_dev)
    if actor_role != required:
        raise ConflictError(
            f"role {actor_role.value} cannot approve at stage {stage.value}; "
            f"required={required.value}"
        )


async def approve_plan(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    actor: str,
    actor_role: ActorRole,
    solo_dev: bool,
) -> Task:
    """T7: ``plan_review -> implementing``."""
    task = await _load_locked(db, task_id)
    if task.status != TaskStatus.PLAN_REVIEW.value:
        raise _forbidden(task, TaskStatus.IMPLEMENTING.value)
    await _matrix_or_forbidden(db, task, ApprovalStage.PLAN, actor_role, solo_dev=solo_dev)
    _record_approval(
        db,
        task_id=task_id,
        stage=ApprovalStage.PLAN,
        decision=ApprovalDecision.APPROVE,
        decided_by=actor,
        decided_by_role=actor_role,
        feedback=None,
    )
    task.status = TaskStatus.IMPLEMENTING.value
    await db.flush()
    await db.refresh(task)
    return task


async def reject_plan(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    actor: str,
    actor_role: ActorRole,
    feedback: str,
    solo_dev: bool,
) -> Task:
    """T8: ``plan_review -> planning`` (rejection)."""
    _require_feedback(feedback)
    task = await _load_locked(db, task_id)
    if task.status != TaskStatus.PLAN_REVIEW.value:
        raise _forbidden(task, TaskStatus.PLANNING.value)
    await _matrix_or_forbidden(db, task, ApprovalStage.PLAN, actor_role, solo_dev=solo_dev)
    _record_approval(
        db,
        task_id=task_id,
        stage=ApprovalStage.PLAN,
        decision=ApprovalDecision.REJECT,
        decided_by=actor,
        decided_by_role=actor_role,
        feedback=feedback,
    )
    task.status = TaskStatus.PLANNING.value
    await db.flush()
    await db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# T9 — submit implementation
# ---------------------------------------------------------------------------


async def submit_implementation(
    db: AsyncSession, task_id: uuid.UUID, *, submitted_by: str
) -> Task:
    """T9: ``implementing -> impl_review``.  TaskImplementation row in T-118."""
    del submitted_by
    task = await _load_locked(db, task_id)
    if task.status != TaskStatus.IMPLEMENTING.value:
        raise _forbidden(task, TaskStatus.IMPL_REVIEW.value)
    task.status = TaskStatus.IMPL_REVIEW.value
    await db.flush()
    await db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# T10 / T11 — review approve / reject
# ---------------------------------------------------------------------------


async def approve_review(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    actor: str,
    actor_role: ActorRole,
    solo_dev: bool,
) -> Task:
    """T10: ``impl_review -> done``."""
    task = await _load_locked(db, task_id)
    if task.status != TaskStatus.IMPL_REVIEW.value:
        raise _forbidden(task, TaskStatus.DONE.value)
    await _matrix_or_forbidden(db, task, ApprovalStage.IMPL, actor_role, solo_dev=solo_dev)
    _record_approval(
        db,
        task_id=task_id,
        stage=ApprovalStage.IMPL,
        decision=ApprovalDecision.APPROVE,
        decided_by=actor,
        decided_by_role=actor_role,
        feedback=None,
    )
    task.status = TaskStatus.DONE.value
    await db.flush()
    await db.refresh(task)
    return task


async def reject_review(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    actor: str,
    actor_role: ActorRole,
    feedback: str,
    solo_dev: bool,
) -> Task:
    """T11: ``impl_review -> implementing`` (rejection)."""
    _require_feedback(feedback)
    task = await _load_locked(db, task_id)
    if task.status != TaskStatus.IMPL_REVIEW.value:
        raise _forbidden(task, TaskStatus.IMPLEMENTING.value)
    await _matrix_or_forbidden(db, task, ApprovalStage.IMPL, actor_role, solo_dev=solo_dev)
    _record_approval(
        db,
        task_id=task_id,
        stage=ApprovalStage.IMPL,
        decision=ApprovalDecision.REJECT,
        decided_by=actor,
        decided_by_role=actor_role,
        feedback=feedback,
    )
    task.status = TaskStatus.IMPLEMENTING.value
    await db.flush()
    await db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# T12 — defer
# ---------------------------------------------------------------------------


async def defer_task(
    db: AsyncSession, task_id: uuid.UUID, *, actor: str, reason: str | None = None
) -> Task:
    """T12: any non-terminal -> ``deferred`` (admin signal).

    Caller is expected to fire :func:`work_items.maybe_advance_to_ready`.
    """
    del actor, reason  # audited via trace / lifecycle_signals; no dedicated column
    task = await _load_locked(db, task_id)
    if task.status in _TERMINAL:
        raise _forbidden(task, TaskStatus.DEFERRED.value)
    task.deferred_from = task.status
    task.status = TaskStatus.DEFERRED.value
    await db.flush()
    await db.refresh(task)
    return task
