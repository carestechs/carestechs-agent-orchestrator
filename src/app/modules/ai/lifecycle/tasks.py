"""Task state-machine transitions (FEAT-006).

Implements transitions T1-T12 from the design doc.  Rejection edges (T3,
T8, T11) preserve owner; feedback required.  Deferral is allowed from any
non-terminal state and fires W5 via the caller.  Every transition holds a
``SELECT ... FOR UPDATE`` on the task row to serialize concurrent writes.

**FEAT-006 rc2 (T-131a/T-132a)**: same mirror-write pattern as
``work_items.py`` — each forward transition accepts optional
``engine`` + ``correlation_id`` and, when the task has an
``engine_item_id``, mirrors the state change onto the flow engine.
Rejection transitions (T3, T8, T11) don't call the engine — their
"transition" keeps the status unchanged; only the ``Approval`` row
records the event.
"""

from __future__ import annotations

import logging
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
from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient
from app.modules.ai.models import Approval, Task, TaskAssignment

logger = logging.getLogger(__name__)

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


async def _mirror_to_engine(
    task: Task,
    to_status: TaskStatus,
    *,
    engine: FlowEngineLifecycleClient | None,
    correlation_id: uuid.UUID | None,
    actor: str | None,
) -> None:
    """Best-effort mirror of a task state change onto the flow engine.

    Swallows engine errors.  Local state is authoritative in rc2 phase 1.
    """
    if engine is None or task.engine_item_id is None:
        return
    try:
        await engine.transition_item(
            item_id=task.engine_item_id,
            to_status=to_status.value,
            correlation_id=correlation_id or uuid.uuid4(),
            actor=actor,
        )
    except Exception:
        logger.warning(
            "engine mirror write failed for task %s -> %s",
            task.id,
            to_status.value,
            exc_info=True,
        )


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
    engine: FlowEngineLifecycleClient | None = None,
    engine_workflow_id: uuid.UUID | None = None,
) -> Task:
    """T1: create a new task in the ``proposed`` state.

    When ``engine`` + ``engine_workflow_id`` are provided, an engine item
    is created and its id stored on the local row.
    """
    engine_item_id: uuid.UUID | None = None
    if engine is not None and engine_workflow_id is not None:
        try:
            engine_item_id = await engine.create_item(
                workflow_id=engine_workflow_id,
                title=title,
                external_ref=external_ref,
                metadata={
                    "work_item_id": str(work_item_id),
                    "proposer_type": proposer_type.value,
                },
            )
        except Exception:
            logger.warning(
                "engine create_item failed for task %s; continuing without mirror",
                external_ref,
                exc_info=True,
            )

    task = Task(
        work_item_id=work_item_id,
        external_ref=external_ref,
        title=title,
        status=TaskStatus.PROPOSED.value,
        proposer_type=proposer_type.value,
        proposer_id=proposer_id,
        engine_item_id=engine_item_id,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# T2 + T4 — approve (advances through assigning automatically)
# ---------------------------------------------------------------------------


async def approve_task(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    actor: str,
    engine: FlowEngineLifecycleClient | None = None,
    correlation_id: uuid.UUID | None = None,
    skip_aux_write: bool = False,
) -> Task:
    """T2+T4: ``proposed -> approved`` then immediately ``approved -> assigning``.

    Writes an ``Approval(stage=proposed, decision=approve)`` row unless
    *skip_aux_write* is True — the caller (signal adapter) then enqueues
    a ``PendingAuxWrite`` and the reactor materializes on webhook arrival
    (FEAT-008/T-167).  Caller is expected to fire
    :func:`work_items.maybe_advance_to_in_progress` after this returns.
    """
    task = await _load_locked(db, task_id)
    if task.status != TaskStatus.PROPOSED.value:
        raise _forbidden(task, TaskStatus.APPROVED.value)
    if not skip_aux_write:
        _record_approval(
            db,
            task_id=task_id,
            stage=ApprovalStage.PROPOSED,
            decision=ApprovalDecision.APPROVE,
            decided_by=actor,
            decided_by_role=ActorRole.ADMIN,
            feedback=None,
        )
    # T4 is inline: approved -> assigning without a separate hop.  Mirror
    # both states to the engine so the audit shows the double hop.
    # FEAT-008/T-169: status write is reactor-managed under engine-present;
    # the inline write here is the engine-absent fallback.
    if engine is None:
        task.status = TaskStatus.ASSIGNING.value
    await db.flush()
    await _mirror_to_engine(
        task, TaskStatus.APPROVED, engine=engine, correlation_id=correlation_id, actor=actor
    )
    await _mirror_to_engine(
        task, TaskStatus.ASSIGNING, engine=engine, correlation_id=correlation_id, actor=actor
    )
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
    engine: FlowEngineLifecycleClient | None = None,
    correlation_id: uuid.UUID | None = None,
    skip_aux_write: bool = False,
) -> tuple[Task, TaskAssignment | None]:
    """T5: ``assigning -> planning``.  Inserts a new TaskAssignment row.

    If an active assignment already exists (reassignment), it is superseded
    in the same transaction (partial-unique index is preserved).  When
    *skip_aux_write* is True (FEAT-008/T-167 engine-present path), the
    new ``TaskAssignment`` row is deferred to the reactor and only the
    supersede of the prior active row happens inline.  The returned
    ``TaskAssignment`` is ``None`` in that case — the caller fetches via
    ``await_reactor`` once the webhook lands.
    """
    task = await _load_locked(db, task_id)
    if task.status != TaskStatus.ASSIGNING.value:
        raise _forbidden(task, TaskStatus.PLANNING.value)

    prior = await _active_assignment(db, task_id)
    if prior is not None:
        prior.superseded_at = datetime.now(UTC)
        await db.flush()

    assignment: TaskAssignment | None = None
    if not skip_aux_write:
        assignment = TaskAssignment(
            task_id=task_id,
            assignee_type=assignee_type.value,
            assignee_id=assignee_id,
            assigned_by=assigned_by,
        )
        db.add(assignment)
    if engine is None:
        task.status = TaskStatus.PLANNING.value
    await db.flush()
    await _mirror_to_engine(
        task,
        TaskStatus.PLANNING,
        engine=engine,
        correlation_id=correlation_id,
        actor=assigned_by,
    )
    await db.refresh(task)
    if assignment is not None:
        await db.refresh(assignment)
    return task, assignment


# ---------------------------------------------------------------------------
# T6 — submit plan
# ---------------------------------------------------------------------------


async def submit_plan(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    submitted_by: str,
    engine: FlowEngineLifecycleClient | None = None,
    correlation_id: uuid.UUID | None = None,
) -> Task:
    """T6: ``planning -> plan_review``.

    Plan-row persistence (``TaskPlan``) lands in T-117; this function only
    advances the task state.
    """
    task = await _load_locked(db, task_id)
    if task.status != TaskStatus.PLANNING.value:
        raise _forbidden(task, TaskStatus.PLAN_REVIEW.value)
    if engine is None:
        task.status = TaskStatus.PLAN_REVIEW.value
    await db.flush()
    await _mirror_to_engine(
        task,
        TaskStatus.PLAN_REVIEW,
        engine=engine,
        correlation_id=correlation_id,
        actor=submitted_by,
    )
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
    engine: FlowEngineLifecycleClient | None = None,
    correlation_id: uuid.UUID | None = None,
    skip_aux_write: bool = False,
) -> Task:
    """T7: ``plan_review -> implementing``."""
    task = await _load_locked(db, task_id)
    if task.status != TaskStatus.PLAN_REVIEW.value:
        raise _forbidden(task, TaskStatus.IMPLEMENTING.value)
    await _matrix_or_forbidden(db, task, ApprovalStage.PLAN, actor_role, solo_dev=solo_dev)
    if not skip_aux_write:
        _record_approval(
            db,
            task_id=task_id,
            stage=ApprovalStage.PLAN,
            decision=ApprovalDecision.APPROVE,
            decided_by=actor,
            decided_by_role=actor_role,
            feedback=None,
        )
    if engine is None:
        task.status = TaskStatus.IMPLEMENTING.value
    await db.flush()
    await _mirror_to_engine(
        task,
        TaskStatus.IMPLEMENTING,
        engine=engine,
        correlation_id=correlation_id,
        actor=actor,
    )
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
    engine: FlowEngineLifecycleClient | None = None,
    correlation_id: uuid.UUID | None = None,
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
    if engine is None:
        task.status = TaskStatus.PLANNING.value
    await db.flush()
    await _mirror_to_engine(
        task,
        TaskStatus.PLANNING,
        engine=engine,
        correlation_id=correlation_id,
        actor=actor,
    )
    await db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# T9 — submit implementation
# ---------------------------------------------------------------------------


async def submit_implementation(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    submitted_by: str,
    engine: FlowEngineLifecycleClient | None = None,
    correlation_id: uuid.UUID | None = None,
) -> Task:
    """T9: ``implementing -> impl_review``.  TaskImplementation row in T-118."""
    task = await _load_locked(db, task_id)
    if task.status != TaskStatus.IMPLEMENTING.value:
        raise _forbidden(task, TaskStatus.IMPL_REVIEW.value)
    if engine is None:
        task.status = TaskStatus.IMPL_REVIEW.value
    await db.flush()
    await _mirror_to_engine(
        task,
        TaskStatus.IMPL_REVIEW,
        engine=engine,
        correlation_id=correlation_id,
        actor=submitted_by,
    )
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
    engine: FlowEngineLifecycleClient | None = None,
    correlation_id: uuid.UUID | None = None,
    skip_aux_write: bool = False,
) -> Task:
    """T10: ``impl_review -> done``."""
    task = await _load_locked(db, task_id)
    if task.status != TaskStatus.IMPL_REVIEW.value:
        raise _forbidden(task, TaskStatus.DONE.value)
    await _matrix_or_forbidden(db, task, ApprovalStage.IMPL, actor_role, solo_dev=solo_dev)
    if not skip_aux_write:
        _record_approval(
            db,
            task_id=task_id,
            stage=ApprovalStage.IMPL,
            decision=ApprovalDecision.APPROVE,
            decided_by=actor,
            decided_by_role=actor_role,
            feedback=None,
        )
    if engine is None:
        task.status = TaskStatus.DONE.value
    await db.flush()
    await _mirror_to_engine(
        task, TaskStatus.DONE, engine=engine, correlation_id=correlation_id, actor=actor
    )
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
    engine: FlowEngineLifecycleClient | None = None,
    correlation_id: uuid.UUID | None = None,
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
    if engine is None:
        task.status = TaskStatus.IMPLEMENTING.value
    await db.flush()
    await _mirror_to_engine(
        task,
        TaskStatus.IMPLEMENTING,
        engine=engine,
        correlation_id=correlation_id,
        actor=actor,
    )
    await db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# T12 — defer
# ---------------------------------------------------------------------------


async def defer_task(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    actor: str,
    reason: str | None = None,
    engine: FlowEngineLifecycleClient | None = None,
    correlation_id: uuid.UUID | None = None,
) -> Task:
    """T12: any non-terminal -> ``deferred`` (admin signal).

    Caller is expected to fire :func:`work_items.maybe_advance_to_ready`.
    """
    del reason  # audited via trace / lifecycle_signals; no dedicated column
    task = await _load_locked(db, task_id)
    if task.status in _TERMINAL:
        raise _forbidden(task, TaskStatus.DEFERRED.value)
    if engine is None:
        task.status = TaskStatus.DEFERRED.value
    await db.flush()
    await _mirror_to_engine(
        task,
        TaskStatus.DEFERRED,
        engine=engine,
        correlation_id=correlation_id,
        actor=actor,
    )
    await db.refresh(task)
    return task
