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
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.ai.enums import RunStatus, StepStatus, StopReason, WebhookEventType


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
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    step_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("steps.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    engine_run_id: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    signature_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    # -- Relationships -----------------------------------------------------
    run: Mapped[Run] = relationship(back_populates="webhook_events", lazy="raise")
    step: Mapped[Step | None] = relationship(back_populates="webhook_events", lazy="raise")

    __table_args__ = (
        _enum_check("event_type", WebhookEventType),
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
