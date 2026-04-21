"""SQLAlchemy models: Run, Step, PolicyCall, WebhookEvent, RunMemory.

All entity shapes match ``docs/data-model.md`` exactly.  Append-only semantics
are documented per class.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import uuid6
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.ai.enums import (
    ActorRole,
    ActorType,
    ApprovalDecision,
    ApprovalStage,
    AssigneeType,
    RunStatus,
    StepStatus,
    StopReason,
    TaskStatus,
    WebhookEventType,
    WebhookSource,
    WorkItemStatus,
    WorkItemType,
)


def generate_uuid7() -> uuid.UUID:
    """Generate a UUIDv7 (time-sortable) primary key."""
    return uuid6.uuid7()


def _enum_check(column: str, enum_cls: type[StrEnum]) -> CheckConstraint:
    """Build a CHECK constraint for a text column against a StrEnum."""
    values = ", ".join(f"'{v.value}'" for v in enum_cls)
    return CheckConstraint(f"{column} IN ({values})", name=f"ck_{column}")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


class Run(Base):
    """A single execution of an agent against a specific intake.

    Mutable: ``status``, ``stop_reason``, ``final_state``, ``ended_at``,
    ``updated_at`` may be updated during the run lifecycle.
    """

    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    agent_ref: Mapped[str] = mapped_column(Text, nullable=False)
    agent_definition_hash: Mapped[str] = mapped_column(Text, nullable=False)
    intake: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default=RunStatus.PENDING)
    stop_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trace_uri: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # -- Relationships (type-checking only; lazy="raise" prevents N+1) -----
    steps: Mapped[list[Step]] = relationship(back_populates="run", lazy="raise")
    policy_calls: Mapped[list[PolicyCall]] = relationship(back_populates="run", lazy="raise")
    webhook_events: Mapped[list[WebhookEvent]] = relationship(back_populates="run", lazy="raise")
    memory: Mapped[RunMemory | None] = relationship(back_populates="run", lazy="raise")

    __table_args__ = (
        _enum_check("status", RunStatus),
        _enum_check("stop_reason", StopReason),
        Index("ix_runs_status_started_at", "status", started_at.desc()),
        Index("ix_runs_agent_ref", "agent_ref"),
    )


# ---------------------------------------------------------------------------
# Step
# ---------------------------------------------------------------------------


class Step(Base):
    """One iteration of the runtime loop.  Append-only once terminal fields are set."""

    __tablename__ = "steps"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    node_name: Mapped[str] = mapped_column(Text, nullable=False)
    node_inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    engine_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default=StepStatus.PENDING)
    node_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # -- Relationships -----------------------------------------------------
    run: Mapped[Run] = relationship(back_populates="steps", lazy="raise")
    policy_call: Mapped[PolicyCall | None] = relationship(back_populates="step", lazy="raise")
    webhook_events: Mapped[list[WebhookEvent]] = relationship(back_populates="step", lazy="raise")

    __table_args__ = (
        _enum_check("status", StepStatus),
        UniqueConstraint("run_id", "step_number", name="uq_steps_run_id_step_number"),
        Index("ix_steps_engine_run_id", "engine_run_id"),
    )


# ---------------------------------------------------------------------------
# PolicyCall
# ---------------------------------------------------------------------------


class PolicyCall(Base):
    """One LLM invocation that produced a decision.  Append-only."""

    __tablename__ = "policy_calls"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    step_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("steps.id"), nullable=False, unique=True)
    prompt_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    available_tools: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    selected_tool: Mapped[str] = mapped_column(Text, nullable=False)
    tool_arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # -- Relationships -----------------------------------------------------
    run: Mapped[Run] = relationship(back_populates="policy_calls", lazy="raise")
    step: Mapped[Step] = relationship(back_populates="policy_call", lazy="raise")

    __table_args__ = (Index("ix_policy_calls_run_id_created_at", "run_id", "created_at"),)


# ---------------------------------------------------------------------------
# WebhookEvent
# ---------------------------------------------------------------------------


class WebhookEvent(Base):
    """Inbound event from the flow engine.  Append-only.

    Every event is persisted before any runtime action — including events with
    ``signature_ok=False``.
    """

    __tablename__ = "webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    # ``run_id`` is NULL for non-engine events (e.g., GitHub webhooks) that
    # correlate to a task rather than a run.
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id"), nullable=True
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("steps.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    engine_run_id: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    signature_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=WebhookSource.ENGINE.value
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    # -- Relationships -----------------------------------------------------
    run: Mapped[Run | None] = relationship(
        back_populates="webhook_events", lazy="raise"
    )
    step: Mapped[Step | None] = relationship(back_populates="webhook_events", lazy="raise")

    __table_args__ = (
        _enum_check("event_type", WebhookEventType),
        _enum_check("source", WebhookSource),
        Index("ix_webhook_events_run_id_received_at", "run_id", "received_at"),
    )


# ---------------------------------------------------------------------------
# RunMemory
# ---------------------------------------------------------------------------


class RunMemory(Base):
    """Per-run agent scratchpad.  Mutable.  One row per run."""

    __tablename__ = "run_memory"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # -- Relationships -----------------------------------------------------
    run: Mapped[Run] = relationship(back_populates="memory", lazy="raise")


# ---------------------------------------------------------------------------
# RunSignal (FEAT-005)
# ---------------------------------------------------------------------------


class RunSignal(Base):
    """Operator-injected signal for an in-flight run.

    v1 supports only ``name='implementation-complete'``; future signal names
    are accepted by the schema (a text column) but only the ones the runtime
    recognizes actually wake the loop.  Append-only.  Idempotent via a
    UNIQUE ``dedupe_key`` derived from ``(run_id, name, task_id)``.
    """

    __tablename__ = "run_signals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_run_signals_dedupe_key"),
        Index("ix_run_signals_run_id_received_at", "run_id", "received_at"),
    )


# ---------------------------------------------------------------------------
# WorkItem (FEAT-006)
# ---------------------------------------------------------------------------


class WorkItem(Base):
    """The deterministic-flow counterpart of a FEAT/BUG/IMP markdown brief.

    Carries the work-item state machine (``open → in_progress ⇄ locked →
    ready → closed``).  ``in_progress`` and ``ready`` are derived transitions
    (fired by the orchestrator after child-task state writes).
    """

    __tablename__ = "work_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    external_ref: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default=WorkItemStatus.OPEN)
    locked_from: Mapped[str | None] = mapped_column(Text, nullable=True)
    # FEAT-006 rc2 (T-131a): nullable for transition.  When the engine
    # client is configured, ``engine_item_id`` is populated at open-time
    # and every transition mirrors the local state change onto the engine.
    engine_item_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True, unique=True
    )
    opened_by: Mapped[str] = mapped_column(Text, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        _enum_check("status", WorkItemStatus),
        _enum_check("type", WorkItemType),
        CheckConstraint(
            "locked_from IS NULL OR locked_from IN ("
            + ", ".join(f"'{v.value}'" for v in WorkItemStatus)
            + ")",
            name="ck_work_items_locked_from",
        ),
        UniqueConstraint("external_ref", name="uq_work_items_external_ref"),
        Index("ix_work_items_status_updated_at", "status", updated_at.desc()),
    )


# ---------------------------------------------------------------------------
# Task (FEAT-006)
# ---------------------------------------------------------------------------


class Task(Base):
    """A single task under a WorkItem.

    Carries the main FEAT-006 state machine.  ``external_ref`` is unique
    within a work item (e.g., ``T-042``); across work items the same ref
    may recur.
    """

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    work_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_items.id", ondelete="RESTRICT"), nullable=False
    )
    external_ref: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default=TaskStatus.PROPOSED)
    # FEAT-006 rc2 (T-131a): mirror-write id on the flow-engine side.
    engine_item_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True, unique=True
    )
    proposer_type: Mapped[str] = mapped_column(Text, nullable=False)
    proposer_id: Mapped[str] = mapped_column(Text, nullable=False)
    deferred_from: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        _enum_check("status", TaskStatus),
        _enum_check("proposer_type", ActorType),
        CheckConstraint(
            "deferred_from IS NULL OR deferred_from IN ("
            + ", ".join(f"'{v.value}'" for v in TaskStatus)
            + ")",
            name="ck_tasks_deferred_from",
        ),
        UniqueConstraint("work_item_id", "external_ref", name="uq_tasks_work_item_ref"),
        Index("ix_tasks_work_item_status", "work_item_id", "status"),
    )


# ---------------------------------------------------------------------------
# TaskAssignment (FEAT-006)
# ---------------------------------------------------------------------------


class TaskAssignment(Base):
    """Append-only record of current and historical task assignments.

    At most one active row per task (where ``superseded_at IS NULL``),
    enforced by a partial-unique index.  Reassignment inserts a new row and
    marks the prior active row superseded in the same transaction.
    """

    __tablename__ = "task_assignments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False
    )
    assignee_type: Mapped[str] = mapped_column(Text, nullable=False)
    assignee_id: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_by: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        _enum_check("assignee_type", AssigneeType),
        Index(
            "ix_task_assignments_active",
            "task_id",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        Index(
            "ix_task_assignments_task_assigned",
            "task_id",
            text("assigned_at DESC"),
        ),
    )


# ---------------------------------------------------------------------------
# Approval (FEAT-006)
# ---------------------------------------------------------------------------


class Approval(Base):
    """Append-only record of every approve/reject decision on a task.

    Rejection iteration count for a ``(task_id, stage)`` pair is derived by
    counting rows with ``decision='reject'``.  No denormalization.
    """

    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False
    )
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by_role: Mapped[str] = mapped_column(Text, nullable=False)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        _enum_check("stage", ApprovalStage),
        _enum_check("decision", ApprovalDecision),
        _enum_check("decided_by_role", ActorRole),
        Index(
            "ix_approvals_task_stage_time",
            "task_id",
            "stage",
            "decided_at",
        ),
    )


# ---------------------------------------------------------------------------
# LifecycleSignal (FEAT-006) — idempotency key store for deterministic-flow
# signals.  Any signal endpoint computes a key over (entity_id, name,
# payload) and records it once; replayed requests hit the UNIQUE constraint
# and short-circuit before running side effects.
# ---------------------------------------------------------------------------


class LifecycleSignal(Base):
    """Records an idempotency key for a processed lifecycle signal."""

    __tablename__ = "lifecycle_signals"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    signal_name: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_lifecycle_signals_entity_name", "entity_id", "signal_name"),
    )


# ---------------------------------------------------------------------------
# TaskPlan (FEAT-006) — append-only submission audit for plan artifacts.
# ---------------------------------------------------------------------------


class TaskPlan(Base):
    """One row per plan submission.  History preserved across revisions."""

    __tablename__ = "task_plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False
    )
    plan_path: Mapped[str] = mapped_column(Text, nullable=False)
    plan_sha: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_by: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_task_plans_task_submitted", "task_id", "submitted_at"),
    )


# ---------------------------------------------------------------------------
# TaskImplementation (FEAT-006) — append-only submission audit for impls.
# ---------------------------------------------------------------------------


class TaskImplementation(Base):
    """One row per implementation submission (agent path or PR webhook)."""

    __tablename__ = "task_implementations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False
    )
    pr_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    commit_sha: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_by: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # FEAT-007: GitHub check-run id posted for this implementation.  NULL
    # when no ``pr_url`` was supplied; the noop sentinel (``"noop"``) is
    # stored when the Checks client is degraded, so later ``update_check``
    # paths can short-circuit without a credential lookup.
    github_check_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_task_implementations_task_submitted",
            "task_id",
            "submitted_at",
        ),
    )


# ---------------------------------------------------------------------------
# EngineWorkflow (FEAT-006 rc2) — caches the engine-side workflow IDs so
# subsequent ``create_item`` calls know which workflow to target.  Written
# once per workflow name at startup; no other code path mutates.
# ---------------------------------------------------------------------------


class EngineWorkflow(Base):
    """Local cache of a flow-engine workflow ID keyed by declared name."""

    __tablename__ = "engine_workflows"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    engine_workflow_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# PendingSignalContext (FEAT-006 rc2 phase 2 / T-133)
# ---------------------------------------------------------------------------


class PendingSignalContext(Base):
    """Threads a signal's payload from adapter → engine → reactor.

    The signal adapter generates a correlation UUID, records the signal
    name + payload here, and passes the UUID to the engine via the
    transition's ``comment`` (``orchestrator-corr:<uuid>``).  When the
    engine webhook arrives, the reactor extracts the UUID from
    ``triggeredBy``, loads this row to recover the original signal
    context, and (eventually — phase-2-final) writes the auxiliary rows
    reactively.  The row is deleted after the reactor consumes it.
    """

    __tablename__ = "pending_signal_context"

    correlation_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    signal_name: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PendingAuxWrite(Base):
    """Outbox row capturing aux-row intent at signal-commit time (FEAT-008).

    The signal adapter enqueues one of these inside its transaction, the
    engine's ``item.transitioned`` webhook triggers the reactor to look
    up by ``correlation_id``, materialize the target aux row (Approval /
    TaskAssignment / TaskPlan / TaskImplementation) from ``payload``, and
    delete this row. Unresolved rows are the recovery surface for
    ``reconcile-aux`` (T-170) when the engine webhook is lost.

    Keyed ``unique(correlation_id)`` so a retrying signal adapter
    idempotently converges on a single outbox row.
    """

    __tablename__ = "pending_aux_writes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    correlation_id: Mapped[uuid.UUID] = mapped_column(nullable=False, unique=True)
    signal_name: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    enqueued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_pending_aux_writes_entity_id", "entity_id"),
        Index("ix_pending_aux_writes_enqueued_at", "enqueued_at"),
    )
