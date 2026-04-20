"""Service-layer adapters for FEAT-006 signal endpoints.

Each adapter:
  1. Computes the idempotency key for the signal + payload.
  2. Calls :func:`idempotency.check_and_record` — on replay, loads the
     current entity state and short-circuits with ``already_received=True``.
  3. Runs the state-machine transition (from ``work_items`` or ``tasks``).
  4. Commits, returns the (entity, already_received) pair.

These functions are thin — business logic stays in the state-machine
modules; these handle the idempotency + commit boundary.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.modules.ai.enums import ActorRole, AssigneeType, WorkItemType
from app.modules.ai.lifecycle import idempotency, tasks, work_items
from app.modules.ai.models import (
    Task,
    TaskAssignment,
    TaskImplementation,
    TaskPlan,
    WorkItem,
)


async def _reload_work_item(db: AsyncSession, work_item_id: uuid.UUID) -> WorkItem:
    row = await db.scalar(select(WorkItem).where(WorkItem.id == work_item_id))
    if row is None:
        raise NotFoundError(f"work item not found: {work_item_id}")
    return row


# ---------------------------------------------------------------------------
# S1 — open work item
# ---------------------------------------------------------------------------


async def dispatch_task_generation(work_item: WorkItem) -> None:
    """Placeholder seam for future agent-driven task generation.

    The follow-up FEAT wires a specific task-generation agent here; in v1
    opening a work item only records the intent.
    """
    import logging

    logging.getLogger(__name__).info(
        "task-generation dispatched for work_item %s (%s)",
        work_item.id,
        work_item.external_ref,
    )


async def open_work_item_signal(
    db: AsyncSession,
    *,
    external_ref: str,
    type: WorkItemType,
    title: str,
    source_path: str | None,
    opened_by: str,
) -> tuple[WorkItem, bool]:
    """S1: create a new work item.  Idempotent on ``external_ref`` + title."""
    # Idempotency scope: admin re-POST with the same external_ref is a no-op.
    # We key off the external_ref itself (not the WI id, which doesn't exist
    # pre-insert).
    payload: Mapping[str, Any] = {
        "externalRef": external_ref,
        "type": type.value,
        "title": title,
        "sourcePath": source_path,
    }
    sentinel_id = uuid.uuid5(uuid.NAMESPACE_OID, f"work-item:{external_ref}")
    key = idempotency.compute_signal_key(sentinel_id, "open-work-item", payload)
    is_new, _ = await idempotency.check_and_record(
        db, key=key, entity_id=sentinel_id, signal_name="open-work-item"
    )

    if not is_new:
        existing = await db.scalar(
            select(WorkItem).where(WorkItem.external_ref == external_ref)
        )
        if existing is None:
            # Extremely unlikely: key recorded but row absent.  Treat as new.
            is_new = True
        else:
            return existing, False

    wi = await work_items.open_work_item(
        db,
        external_ref=external_ref,
        type=type,
        title=title,
        source_path=source_path,
        opened_by=opened_by,
    )
    await db.commit()
    await dispatch_task_generation(wi)
    return wi, True


# ---------------------------------------------------------------------------
# S2/S3/S4 — lock / unlock / close
# ---------------------------------------------------------------------------


_WorkItemTransition = Callable[..., Awaitable[WorkItem]]


async def _guarded_work_item_signal(
    db: AsyncSession,
    *,
    work_item_id: uuid.UUID,
    signal_name: str,
    payload: Mapping[str, Any],
    transition: _WorkItemTransition,
    actor: str,
) -> tuple[WorkItem, bool]:
    """Shared body for S2/S3/S4.

    If the idempotency key is seen twice, we still need to return the
    current WorkItem row — the entity is the caller's source of truth.
    """
    key = idempotency.compute_signal_key(work_item_id, signal_name, payload)
    is_new, _ = await idempotency.check_and_record(
        db, key=key, entity_id=work_item_id, signal_name=signal_name
    )
    if not is_new:
        return await _reload_work_item(db, work_item_id), False

    # NOTE: A transition failure after key insertion means the key persists
    # on the caller's transaction.  In production the request handler's
    # top-level rollback on the raised error will undo both.  In the test
    # harness (SAVEPOINT-wrapped session) the outer rollback is deferred
    # to test teardown, which is also fine.  Operators retrying after a
    # 409 must vary the payload (or drop the key manually) to re-attempt.
    wi = await transition(db, work_item_id, actor=actor)
    await db.commit()
    return wi, True


async def lock_work_item_signal(
    db: AsyncSession,
    work_item_id: uuid.UUID,
    *,
    reason: str | None,
    actor: str,
) -> tuple[WorkItem, bool]:
    """S2: admin pause."""
    return await _guarded_work_item_signal(
        db,
        work_item_id=work_item_id,
        signal_name="lock-work-item",
        payload={"reason": reason},
        transition=work_items.lock_work_item,
        actor=actor,
    )


async def unlock_work_item_signal(
    db: AsyncSession,
    work_item_id: uuid.UUID,
    *,
    actor: str,
) -> tuple[WorkItem, bool]:
    """S3: admin resume."""
    return await _guarded_work_item_signal(
        db,
        work_item_id=work_item_id,
        signal_name="unlock-work-item",
        payload={},
        transition=work_items.unlock_work_item,
        actor=actor,
    )


async def close_work_item_signal(
    db: AsyncSession,
    work_item_id: uuid.UUID,
    *,
    notes: str | None,
    actor: str,
) -> tuple[WorkItem, bool]:
    """S4: admin close (requires ``ready``)."""
    return await _guarded_work_item_signal(
        db,
        work_item_id=work_item_id,
        signal_name="close-work-item",
        payload={"notes": notes},
        transition=work_items.close_work_item,
        actor=actor,
    )


# ---------------------------------------------------------------------------
# Task signals (S5-S14)
# ---------------------------------------------------------------------------


async def _reload_task(db: AsyncSession, task_id: uuid.UUID) -> Task:
    row = await db.scalar(select(Task).where(Task.id == task_id))
    if row is None:
        raise NotFoundError(f"task not found: {task_id}")
    return row


async def approve_task_signal(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    actor: str,
) -> tuple[Task, bool]:
    """S5: admin approves a proposed task.

    Fires T4 (approved -> assigning) inline and calls
    :func:`work_items.maybe_advance_to_in_progress` so W2 fires on the first
    approved task in a work item.
    """
    key = idempotency.compute_signal_key(task_id, "approve-task", {})
    is_new, _ = await idempotency.check_and_record(
        db, key=key, entity_id=task_id, signal_name="approve-task"
    )
    if not is_new:
        return await _reload_task(db, task_id), False

    task = await tasks.approve_task(db, task_id, actor=actor)
    await work_items.maybe_advance_to_in_progress(db, task.work_item_id)
    await db.commit()
    return task, True


async def reject_task_signal(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    feedback: str,
    actor: str,
) -> tuple[Task, bool]:
    """S6: admin rejects a proposed task with non-empty feedback."""
    payload = {"feedback": feedback}
    key = idempotency.compute_signal_key(task_id, "reject-task", payload)
    is_new, _ = await idempotency.check_and_record(
        db, key=key, entity_id=task_id, signal_name="reject-task"
    )
    if not is_new:
        return await _reload_task(db, task_id), False

    task = await tasks.reject_task_proposal(
        db, task_id, actor=actor, feedback=feedback
    )
    await db.commit()
    return task, True


async def assign_task_signal(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    assignee_type: AssigneeType,
    assignee_id: str,
    actor: str,
) -> tuple[Task, TaskAssignment, bool]:
    """S7: admin assigns the task.  ``assigning -> planning``."""
    payload = {
        "assigneeType": assignee_type.value,
        "assigneeId": assignee_id,
    }
    key = idempotency.compute_signal_key(task_id, "assign-task", payload)
    is_new, _ = await idempotency.check_and_record(
        db, key=key, entity_id=task_id, signal_name="assign-task"
    )
    if not is_new:
        task = await _reload_task(db, task_id)
        active = await db.scalar(
            select(TaskAssignment).where(
                TaskAssignment.task_id == task_id,
                TaskAssignment.superseded_at.is_(None),
            )
        )
        assert active is not None, "idempotent replay must find an active assignment"
        return task, active, False

    task, assignment = await tasks.assign_task(
        db,
        task_id,
        assignee_type=assignee_type,
        assignee_id=assignee_id,
        assigned_by=actor,
    )
    await db.commit()
    return task, assignment, True


async def defer_task_signal(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    reason: str | None,
    actor: str,
) -> tuple[Task, bool]:
    """S14: admin defers a non-terminal task.  Fires W5 derivation."""
    payload = {"reason": reason}
    key = idempotency.compute_signal_key(task_id, "defer-task", payload)
    is_new, _ = await idempotency.check_and_record(
        db, key=key, entity_id=task_id, signal_name="defer-task"
    )
    if not is_new:
        return await _reload_task(db, task_id), False

    task = await tasks.defer_task(db, task_id, actor=actor, reason=reason)
    await work_items.maybe_advance_to_ready(db, task.work_item_id)
    await db.commit()
    return task, True


# ---------------------------------------------------------------------------
# Plan signals (S8, S9, S10)
# ---------------------------------------------------------------------------


async def submit_plan_signal(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    plan_path: str,
    plan_sha: str,
    actor: str,
) -> tuple[Task, bool]:
    """S8: submit a plan.  Inserts a ``TaskPlan`` row and advances state."""
    payload = {"planPath": plan_path, "planSha": plan_sha}
    key = idempotency.compute_signal_key(task_id, "submit-plan", payload)
    is_new, _ = await idempotency.check_and_record(
        db, key=key, entity_id=task_id, signal_name="submit-plan"
    )
    if not is_new:
        return await _reload_task(db, task_id), False

    task = await tasks.submit_plan(db, task_id, submitted_by=actor)
    db.add(
        TaskPlan(
            task_id=task_id,
            plan_path=plan_path,
            plan_sha=plan_sha,
            submitted_by=actor,
        )
    )
    await db.commit()
    return task, True


async def approve_plan_signal(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    actor: str,
    actor_role: ActorRole,
    solo_dev: bool,
) -> tuple[Task, bool]:
    """S9: approve plan — matrix-derived role check inside the transition.

    Idempotency key includes ``actor_role`` so a legitimate retry after a
    matrix-mismatch 409 (wrong role) with the *correct* role is not
    short-circuited by the first attempt.
    """
    key = idempotency.compute_signal_key(
        task_id, "approve-plan", {"actorRole": actor_role.value}
    )
    is_new, _ = await idempotency.check_and_record(
        db, key=key, entity_id=task_id, signal_name="approve-plan"
    )
    if not is_new:
        return await _reload_task(db, task_id), False

    task = await tasks.approve_plan(
        db, task_id, actor=actor, actor_role=actor_role, solo_dev=solo_dev
    )
    await db.commit()
    return task, True


async def reject_plan_signal(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    feedback: str,
    actor: str,
    actor_role: ActorRole,
    solo_dev: bool,
) -> tuple[Task, bool]:
    """S10: reject plan with feedback."""
    payload = {"feedback": feedback, "actorRole": actor_role.value}
    key = idempotency.compute_signal_key(task_id, "reject-plan", payload)
    is_new, _ = await idempotency.check_and_record(
        db, key=key, entity_id=task_id, signal_name="reject-plan"
    )
    if not is_new:
        return await _reload_task(db, task_id), False

    task = await tasks.reject_plan(
        db,
        task_id,
        actor=actor,
        actor_role=actor_role,
        feedback=feedback,
        solo_dev=solo_dev,
    )
    await db.commit()
    return task, True


# ---------------------------------------------------------------------------
# Implementation + review signals (S11, S12, S13)
# ---------------------------------------------------------------------------


async def submit_implementation_signal(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    pr_url: str | None,
    commit_sha: str,
    summary: str,
    actor: str,
) -> tuple[Task, bool]:
    """S11 (agent path): submit an implementation for review.

    Inserts ``TaskImplementation`` and advances ``implementing -> impl_review``.
    """
    payload = {"prUrl": pr_url, "commitSha": commit_sha, "summary": summary}
    key = idempotency.compute_signal_key(task_id, "submit-implementation", payload)
    is_new, _ = await idempotency.check_and_record(
        db, key=key, entity_id=task_id, signal_name="submit-implementation"
    )
    if not is_new:
        return await _reload_task(db, task_id), False

    task = await tasks.submit_implementation(db, task_id, submitted_by=actor)
    db.add(
        TaskImplementation(
            task_id=task_id,
            pr_url=pr_url,
            commit_sha=commit_sha,
            summary=summary,
            submitted_by=actor,
        )
    )
    await db.commit()
    return task, True


async def approve_review_signal(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    actor: str,
    actor_role: ActorRole,
    solo_dev: bool,
) -> tuple[Task, bool]:
    """S12: approve implementation review; fires W5."""
    key = idempotency.compute_signal_key(
        task_id, "approve-review", {"actorRole": actor_role.value}
    )
    is_new, _ = await idempotency.check_and_record(
        db, key=key, entity_id=task_id, signal_name="approve-review"
    )
    if not is_new:
        return await _reload_task(db, task_id), False

    task = await tasks.approve_review(
        db, task_id, actor=actor, actor_role=actor_role, solo_dev=solo_dev
    )
    await work_items.maybe_advance_to_ready(db, task.work_item_id)
    await db.commit()
    return task, True


async def reject_review_signal(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    feedback: str,
    actor: str,
    actor_role: ActorRole,
    solo_dev: bool,
) -> tuple[Task, bool]:
    """S13: reject implementation review with feedback."""
    payload = {"feedback": feedback, "actorRole": actor_role.value}
    key = idempotency.compute_signal_key(task_id, "reject-review", payload)
    is_new, _ = await idempotency.check_and_record(
        db, key=key, entity_id=task_id, signal_name="reject-review"
    )
    if not is_new:
        return await _reload_task(db, task_id), False

    task = await tasks.reject_review(
        db,
        task_id,
        actor=actor,
        actor_role=actor_role,
        feedback=feedback,
        solo_dev=solo_dev,
    )
    await db.commit()
    return task, True
