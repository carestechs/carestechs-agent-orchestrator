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

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.exceptions import NotFoundError, ValidationError
from app.modules.ai.enums import ActorRole, AssigneeType, TaskStatus, WorkItemType
from app.modules.ai.github.checks import GitHubChecksClient
from app.modules.ai.lifecycle import idempotency, tasks, work_items
from app.modules.ai.lifecycle.effectors import (
    EffectorContext,
    dispatch_effector,
)
from app.modules.ai.lifecycle.effectors.github import (
    GitHubCheckCreateEffector,
    GitHubCheckUpdateEffector,
)
from app.modules.ai.lifecycle.effectors.task_generation import (
    GenerateTasksEffector,
)
from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient
from app.modules.ai.models import (
    PendingAuxWrite,
    PendingSignalContext,
    Task,
    TaskAssignment,
    TaskImplementation,
    TaskPlan,
    WorkItem,
)
from app.modules.ai.trace import get_trace_store

logger = logging.getLogger(__name__)


async def _dispatch_github_check_create(
    db: AsyncSession,
    *,
    github: GitHubChecksClient,
    task_id: uuid.UUID,
    correlation_id: uuid.UUID,
) -> None:
    """Fire the GitHubCheckCreateEffector for this task.

    FEAT-008/T-162: the inline ``_post_create_check`` helper moved into
    :class:`GitHubCheckCreateEffector`; this function dispatches it with
    a per-call ``EffectorContext`` so FEAT-007's DI-override pattern
    (tests override ``get_github_checks_client_dep``) keeps working
    unchanged. ``dispatch_effector`` emits the standard ``effector_call``
    trace; exceptions from the effector are caught and surfaced in the
    result (they do not propagate) — except ``ValidationError`` on an
    invalid ``pr_url``, which callers want as a 400.
    """
    effector = GitHubCheckCreateEffector(github=github)
    ctx = EffectorContext(
        entity_type="task",
        entity_id=task_id,
        from_state=TaskStatus.IMPLEMENTING.value,
        to_state=TaskStatus.IMPL_REVIEW.value,
        transition="T9",
        correlation_id=correlation_id,
        db=db,
        settings=get_settings(),
    )
    try:
        await dispatch_effector(effector, ctx, get_trace_store())
    except ValidationError:
        # Malformed URL with a real client configured — bubble up so
        # the route handler maps to 400.
        raise


async def _dispatch_github_check_update(
    db: AsyncSession,
    *,
    github: GitHubChecksClient,
    task_id: uuid.UUID,
    conclusion: Literal["success", "failure"],
    correlation_id: uuid.UUID,
) -> None:
    """Fire the GitHubCheckUpdateEffector for this task.

    T10 / T11 target states differ (``done`` vs ``implementing``); the
    effector is constructed with the right conclusion per caller.
    """
    to_state = TaskStatus.DONE.value if conclusion == "success" else TaskStatus.IMPLEMENTING.value
    transition = "T10" if conclusion == "success" else "T11"
    effector = GitHubCheckUpdateEffector(github=github, conclusion=conclusion)
    ctx = EffectorContext(
        entity_type="task",
        entity_id=task_id,
        from_state=TaskStatus.IMPL_REVIEW.value,
        to_state=to_state,
        transition=transition,
        correlation_id=correlation_id,
        db=db,
        settings=get_settings(),
    )
    await dispatch_effector(effector, ctx, get_trace_store())


async def _with_correlation(
    db: AsyncSession,
    *,
    signal_name: str,
    payload: Mapping[str, Any],
) -> uuid.UUID:
    """Record a PendingSignalContext row and return its correlation id.

    The caller threads the returned UUID to the engine via the transition's
    comment so the reactor (on webhook arrival) can recover this signal's
    payload and write auxiliary rows reactively (phase-2-final).
    """
    corr = uuid.uuid4()
    db.add(
        PendingSignalContext(
            correlation_id=corr,
            signal_name=signal_name,
            payload=dict(payload),
        )
    )
    await db.flush()
    return corr


def _enqueue_aux(
    db: AsyncSession,
    *,
    correlation_id: uuid.UUID,
    signal_name: str,
    entity_id: uuid.UUID,
    entity_type: str,
    aux_type: str,
    fields: Mapping[str, Any],
) -> None:
    """Append a ``PendingAuxWrite`` for the reactor to materialize on webhook
    (FEAT-008/T-167).

    Same transaction as the signal's idempotency key + state transition;
    committed together by the adapter.  Payload carries enough to rebuild
    the target aux row — the reactor dispatches on ``aux_type``.
    """
    db.add(
        PendingAuxWrite(
            correlation_id=correlation_id,
            signal_name=signal_name,
            entity_type=entity_type,
            entity_id=entity_id,
            payload={"aux_type": aux_type, **dict(fields)},
        )
    )


async def _reload_work_item(db: AsyncSession, work_item_id: uuid.UUID) -> WorkItem:
    row = await db.scalar(select(WorkItem).where(WorkItem.id == work_item_id))
    if row is None:
        raise NotFoundError(f"work item not found: {work_item_id}")
    return row


# ---------------------------------------------------------------------------
# S1 — open work item
# ---------------------------------------------------------------------------


async def _dispatch_task_generation(db: AsyncSession, *, work_item_id: uuid.UUID) -> None:
    """Fire the GenerateTasksEffector for a freshly-opened work item.

    FEAT-008/T-164: replaces the log-only ``dispatch_task_generation``
    stub. Runs deterministically (no LLM, no brief parsing); an
    LLM-backed generator will replace this under the same registry key
    (``work_item:entry:open``) when it lands.

    Direct dispatch is preserved (alongside T-173's reactor-driven
    ``fire_all``) because the engine is not guaranteed to emit
    ``item.transitioned`` for the initial entry into ``open`` — and even
    when it does, mock-engine tests don't auto-fire webhooks. If the real
    engine *does* emit an entry webhook, the effector's idempotency guard
    (``status='skipped'`` when tasks already exist) absorbs the duplicate.
    """
    effector = GenerateTasksEffector()
    ctx = EffectorContext(
        entity_type="work_item",
        entity_id=work_item_id,
        from_state=None,
        to_state="open",
        transition="S1",
        correlation_id=None,
        db=db,
        settings=get_settings(),
    )
    await dispatch_effector(effector, ctx, get_trace_store())


async def open_work_item_signal(
    db: AsyncSession,
    *,
    external_ref: str,
    type: WorkItemType,
    title: str,
    source_path: str | None,
    opened_by: str,
    engine: FlowEngineLifecycleClient | None = None,
    engine_workflow_id: uuid.UUID | None = None,
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
    is_new, _ = await idempotency.check_and_record(db, key=key, entity_id=sentinel_id, signal_name="open-work-item")

    if not is_new:
        existing = await db.scalar(select(WorkItem).where(WorkItem.external_ref == external_ref))
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
        engine=engine,
        engine_workflow_id=engine_workflow_id,
    )
    await db.commit()
    await _dispatch_task_generation(db, work_item_id=wi.id)
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
    engine: FlowEngineLifecycleClient | None = None,
) -> tuple[WorkItem, bool]:
    """Shared body for S2/S3/S4.

    If the idempotency key is seen twice, we still need to return the
    current WorkItem row — the entity is the caller's source of truth.
    """
    key = idempotency.compute_signal_key(work_item_id, signal_name, payload)
    is_new, _ = await idempotency.check_and_record(db, key=key, entity_id=work_item_id, signal_name=signal_name)
    if not is_new:
        return await _reload_work_item(db, work_item_id), False

    # NOTE: A transition failure after key insertion means the key persists
    # on the caller's transaction.  In production the request handler's
    # top-level rollback on the raised error will undo both.  In the test
    # harness (SAVEPOINT-wrapped session) the outer rollback is deferred
    # to test teardown, which is also fine.  Operators retrying after a
    # 409 must vary the payload (or drop the key manually) to re-attempt.
    wi = await transition(db, work_item_id, actor=actor, engine=engine)
    await db.commit()
    return wi, True


async def lock_work_item_signal(
    db: AsyncSession,
    work_item_id: uuid.UUID,
    *,
    reason: str | None,
    actor: str,
    engine: FlowEngineLifecycleClient | None = None,
) -> tuple[WorkItem, bool]:
    """S2: admin pause."""
    return await _guarded_work_item_signal(
        db,
        work_item_id=work_item_id,
        signal_name="lock-work-item",
        payload={"reason": reason},
        transition=work_items.lock_work_item,
        actor=actor,
        engine=engine,
    )


async def unlock_work_item_signal(
    db: AsyncSession,
    work_item_id: uuid.UUID,
    *,
    actor: str,
    engine: FlowEngineLifecycleClient | None = None,
) -> tuple[WorkItem, bool]:
    """S3: admin resume."""
    return await _guarded_work_item_signal(
        db,
        work_item_id=work_item_id,
        signal_name="unlock-work-item",
        payload={},
        transition=work_items.unlock_work_item,
        actor=actor,
        engine=engine,
    )


async def close_work_item_signal(
    db: AsyncSession,
    work_item_id: uuid.UUID,
    *,
    notes: str | None,
    actor: str,
    engine: FlowEngineLifecycleClient | None = None,
) -> tuple[WorkItem, bool]:
    """S4: admin close (requires ``ready``)."""
    return await _guarded_work_item_signal(
        db,
        work_item_id=work_item_id,
        signal_name="close-work-item",
        payload={"notes": notes},
        transition=work_items.close_work_item,
        actor=actor,
        engine=engine,
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
    engine: FlowEngineLifecycleClient | None = None,
) -> tuple[Task, bool]:
    """S5: admin approves a proposed task.

    Fires T4 (approved -> assigning) inline and calls
    :func:`work_items.maybe_advance_to_in_progress` so W2 fires on the first
    approved task in a work item.
    """
    key = idempotency.compute_signal_key(task_id, "approve-task", {})
    is_new, _ = await idempotency.check_and_record(db, key=key, entity_id=task_id, signal_name="approve-task")
    if not is_new:
        return await _reload_task(db, task_id), False

    corr = await _with_correlation(
        db,
        signal_name="approve-task",
        payload={"taskId": str(task_id), "actor": actor},
    )
    task = await tasks.approve_task(
        db,
        task_id,
        actor=actor,
        engine=engine,
        correlation_id=corr,
        skip_aux_write=engine is not None,
    )
    if engine is not None:
        _enqueue_aux(
            db,
            correlation_id=corr,
            signal_name="approve-task",
            entity_id=task_id,
            entity_type="task",
            aux_type="approval",
            fields={
                "stage": "proposed",
                "decision": "approve",
                "decided_by": actor,
                "decided_by_role": ActorRole.ADMIN.value,
            },
        )
    await work_items.maybe_advance_to_in_progress(db, task.work_item_id, engine=engine)
    await db.commit()
    return task, True


async def reject_task_signal(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    feedback: str,
    actor: str,
    engine: FlowEngineLifecycleClient | None = None,
) -> tuple[Task, bool]:
    """S6: admin rejects a proposed task with non-empty feedback."""
    payload = {"feedback": feedback}
    key = idempotency.compute_signal_key(task_id, "reject-task", payload)
    is_new, _ = await idempotency.check_and_record(db, key=key, entity_id=task_id, signal_name="reject-task")
    if not is_new:
        return await _reload_task(db, task_id), False

    # Rejection does NOT transition the task in the engine (status stays
    # `proposed`); only the Approval row records it.  We still record a
    # correlation context row so that phase-2-final can look up the
    # rejection payload if/when we surface rejections via engine events.
    await _with_correlation(
        db,
        signal_name="reject-task",
        payload={"taskId": str(task_id), "feedback": feedback},
    )
    task = await tasks.reject_task_proposal(db, task_id, actor=actor, feedback=feedback)
    await db.commit()
    return task, True


async def assign_task_signal(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    assignee_type: AssigneeType,
    assignee_id: str,
    actor: str,
    engine: FlowEngineLifecycleClient | None = None,
) -> tuple[Task, TaskAssignment | None, bool]:
    """S7: admin assigns the task.  ``assigning -> planning``.

    Returns the ``TaskAssignment`` row when it was written inline
    (engine-absent fallback), or ``None`` when the assignment is
    deferred to the reactor (FEAT-008/T-167 engine-present path) —
    callers poll via :func:`tests.integration._reactor_helpers.await_reactor`
    to observe the materialized row.
    """
    payload = {
        "assigneeType": assignee_type.value,
        "assigneeId": assignee_id,
    }
    key = idempotency.compute_signal_key(task_id, "assign-task", payload)
    is_new, _ = await idempotency.check_and_record(db, key=key, entity_id=task_id, signal_name="assign-task")
    if not is_new:
        task = await _reload_task(db, task_id)
        # Replay path may legitimately see no active assignment row under
        # engine-present mode if the reactor hasn't materialized it yet
        # (T-167 outbox). Callers treat ``None`` as "assignment pending".
        active = await db.scalar(
            select(TaskAssignment).where(
                TaskAssignment.task_id == task_id,
                TaskAssignment.superseded_at.is_(None),
            )
        )
        return task, active, False

    corr = await _with_correlation(
        db,
        signal_name="assign-task",
        payload={
            "taskId": str(task_id),
            "assigneeType": assignee_type.value,
            "assigneeId": assignee_id,
        },
    )
    task, assignment = await tasks.assign_task(
        db,
        task_id,
        assignee_type=assignee_type,
        assignee_id=assignee_id,
        assigned_by=actor,
        engine=engine,
        correlation_id=corr,
        skip_aux_write=engine is not None,
    )
    if engine is not None:
        _enqueue_aux(
            db,
            correlation_id=corr,
            signal_name="assign-task",
            entity_id=task_id,
            entity_type="task",
            aux_type="task_assignment",
            fields={
                "assignee_type": assignee_type.value,
                "assignee_id": assignee_id,
                "assigned_by": actor,
            },
        )
    await db.commit()
    if engine is not None:
        # The TaskAssignment row lands via the reactor on webhook arrival.
        # Signal callers that need a concrete row should poll via
        # ``await_reactor`` against the active-assignment index.
        assert assignment is None
    return task, assignment, True


async def defer_task_signal(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    reason: str | None,
    actor: str,
    engine: FlowEngineLifecycleClient | None = None,
) -> tuple[Task, bool]:
    """S14: admin defers a non-terminal task.  Fires W5 derivation."""
    payload = {"reason": reason}
    key = idempotency.compute_signal_key(task_id, "defer-task", payload)
    is_new, _ = await idempotency.check_and_record(db, key=key, entity_id=task_id, signal_name="defer-task")
    if not is_new:
        return await _reload_task(db, task_id), False

    corr = await _with_correlation(
        db,
        signal_name="defer-task",
        payload={"taskId": str(task_id), "reason": reason},
    )
    task = await tasks.defer_task(db, task_id, actor=actor, reason=reason, engine=engine, correlation_id=corr)
    await work_items.maybe_advance_to_ready(db, task.work_item_id, engine=engine)
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
    engine: FlowEngineLifecycleClient | None = None,
) -> tuple[Task, bool]:
    """S8: submit a plan.  Inserts a ``TaskPlan`` row and advances state."""
    payload = {"planPath": plan_path, "planSha": plan_sha}
    key = idempotency.compute_signal_key(task_id, "submit-plan", payload)
    is_new, _ = await idempotency.check_and_record(db, key=key, entity_id=task_id, signal_name="submit-plan")
    if not is_new:
        return await _reload_task(db, task_id), False

    corr = await _with_correlation(
        db,
        signal_name="submit-plan",
        payload={
            "taskId": str(task_id),
            "planPath": plan_path,
            "planSha": plan_sha,
            "submittedBy": actor,
        },
    )
    task = await tasks.submit_plan(db, task_id, submitted_by=actor, engine=engine, correlation_id=corr)
    if engine is not None:
        _enqueue_aux(
            db,
            correlation_id=corr,
            signal_name="submit-plan",
            entity_id=task_id,
            entity_type="task",
            aux_type="task_plan",
            fields={
                "plan_path": plan_path,
                "plan_sha": plan_sha,
                "submitted_by": actor,
            },
        )
    else:
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
    engine: FlowEngineLifecycleClient | None = None,
) -> tuple[Task, bool]:
    """S9: approve plan — matrix-derived role check inside the transition.

    Idempotency key includes ``actor_role`` so a legitimate retry after a
    matrix-mismatch 409 (wrong role) with the *correct* role is not
    short-circuited by the first attempt.
    """
    key = idempotency.compute_signal_key(task_id, "approve-plan", {"actorRole": actor_role.value})
    is_new, _ = await idempotency.check_and_record(db, key=key, entity_id=task_id, signal_name="approve-plan")
    if not is_new:
        return await _reload_task(db, task_id), False

    corr = await _with_correlation(
        db,
        signal_name="approve-plan",
        payload={
            "taskId": str(task_id),
            "actor": actor,
            "actorRole": actor_role.value,
        },
    )
    task = await tasks.approve_plan(
        db,
        task_id,
        actor=actor,
        actor_role=actor_role,
        solo_dev=solo_dev,
        engine=engine,
        correlation_id=corr,
        skip_aux_write=engine is not None,
    )
    if engine is not None:
        _enqueue_aux(
            db,
            correlation_id=corr,
            signal_name="approve-plan",
            entity_id=task_id,
            entity_type="task",
            aux_type="approval",
            fields={
                "stage": "plan",
                "decision": "approve",
                "decided_by": actor,
                "decided_by_role": actor_role.value,
            },
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
    engine: FlowEngineLifecycleClient | None = None,
) -> tuple[Task, bool]:
    """S10: reject plan with feedback."""
    payload = {"feedback": feedback, "actorRole": actor_role.value}
    key = idempotency.compute_signal_key(task_id, "reject-plan", payload)
    is_new, _ = await idempotency.check_and_record(db, key=key, entity_id=task_id, signal_name="reject-plan")
    if not is_new:
        return await _reload_task(db, task_id), False

    corr = await _with_correlation(
        db,
        signal_name="reject-plan",
        payload={
            "taskId": str(task_id),
            "feedback": feedback,
            "actorRole": actor_role.value,
        },
    )
    task = await tasks.reject_plan(
        db,
        task_id,
        actor=actor,
        actor_role=actor_role,
        feedback=feedback,
        solo_dev=solo_dev,
        engine=engine,
        correlation_id=corr,
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
    engine: FlowEngineLifecycleClient | None = None,
    github: GitHubChecksClient | None = None,
) -> tuple[Task, bool]:
    """S11 (agent path): submit an implementation for review.

    Inserts ``TaskImplementation`` and advances ``implementing -> impl_review``.
    When *pr_url* + *github* are supplied, registers the merge-gate check
    after the transition commits (non-fatal).
    """
    payload = {"prUrl": pr_url, "commitSha": commit_sha, "summary": summary}
    key = idempotency.compute_signal_key(task_id, "submit-implementation", payload)
    is_new, _ = await idempotency.check_and_record(db, key=key, entity_id=task_id, signal_name="submit-implementation")
    if not is_new:
        return await _reload_task(db, task_id), False

    corr = await _with_correlation(
        db,
        signal_name="submit-implementation",
        payload={
            "taskId": str(task_id),
            "prUrl": pr_url,
            "commitSha": commit_sha,
            "summary": summary,
        },
    )
    task = await tasks.submit_implementation(db, task_id, submitted_by=actor, engine=engine, correlation_id=corr)
    if engine is not None:
        _enqueue_aux(
            db,
            correlation_id=corr,
            signal_name="submit-implementation",
            entity_id=task_id,
            entity_type="task",
            aux_type="task_implementation",
            fields={
                "pr_url": pr_url,
                "commit_sha": commit_sha,
                "summary": summary,
                "submitted_by": actor,
            },
        )
    else:
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

    if pr_url is not None and github is not None:
        await _dispatch_github_check_create(
            db,
            github=github,
            task_id=task_id,
            correlation_id=corr,
        )

    return task, True


async def approve_review_signal(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    actor: str,
    actor_role: ActorRole,
    solo_dev: bool,
    engine: FlowEngineLifecycleClient | None = None,
    github: GitHubChecksClient | None = None,
) -> tuple[Task, bool]:
    """S12: approve implementation review; fires W5."""
    key = idempotency.compute_signal_key(task_id, "approve-review", {"actorRole": actor_role.value})
    is_new, _ = await idempotency.check_and_record(db, key=key, entity_id=task_id, signal_name="approve-review")
    if not is_new:
        return await _reload_task(db, task_id), False

    corr = await _with_correlation(
        db,
        signal_name="approve-review",
        payload={
            "taskId": str(task_id),
            "actor": actor,
            "actorRole": actor_role.value,
        },
    )
    task = await tasks.approve_review(
        db,
        task_id,
        actor=actor,
        actor_role=actor_role,
        solo_dev=solo_dev,
        engine=engine,
        correlation_id=corr,
        skip_aux_write=engine is not None,
    )
    if engine is not None:
        _enqueue_aux(
            db,
            correlation_id=corr,
            signal_name="approve-review",
            entity_id=task_id,
            entity_type="task",
            aux_type="approval",
            fields={
                "stage": "impl",
                "decision": "approve",
                "decided_by": actor,
                "decided_by_role": actor_role.value,
            },
        )
    await work_items.maybe_advance_to_ready(db, task.work_item_id, engine=engine)
    await db.commit()

    if github is not None:
        await _dispatch_github_check_update(
            db,
            github=github,
            task_id=task_id,
            conclusion="success",
            correlation_id=corr,
        )

    return task, True


async def reject_review_signal(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    feedback: str,
    actor: str,
    actor_role: ActorRole,
    solo_dev: bool,
    engine: FlowEngineLifecycleClient | None = None,
    github: GitHubChecksClient | None = None,
) -> tuple[Task, bool]:
    """S13: reject implementation review with feedback."""
    payload = {"feedback": feedback, "actorRole": actor_role.value}
    key = idempotency.compute_signal_key(task_id, "reject-review", payload)
    is_new, _ = await idempotency.check_and_record(db, key=key, entity_id=task_id, signal_name="reject-review")
    if not is_new:
        return await _reload_task(db, task_id), False

    corr = await _with_correlation(
        db,
        signal_name="reject-review",
        payload={
            "taskId": str(task_id),
            "feedback": feedback,
            "actorRole": actor_role.value,
        },
    )
    task = await tasks.reject_review(
        db,
        task_id,
        actor=actor,
        actor_role=actor_role,
        feedback=feedback,
        solo_dev=solo_dev,
        engine=engine,
        correlation_id=corr,
    )
    await db.commit()

    if github is not None:
        await _dispatch_github_check_update(
            db,
            github=github,
            task_id=task_id,
            conclusion="failure",
            correlation_id=corr,
        )

    return task, True
