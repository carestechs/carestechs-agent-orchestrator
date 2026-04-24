"""Pydantic DTOs mirroring docs/api-spec.md.

All fields use snake_case Python with camelCase JSON aliases via
``alias_generator``.  Enums are shared with SQLAlchemy models via
``enums.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

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

# ---------------------------------------------------------------------------
# Base config — shared by all DTOs
# ---------------------------------------------------------------------------

_CAMEL_CONFIG = ConfigDict(populate_by_name=True, alias_generator=to_camel)

# ---------------------------------------------------------------------------
# Response DTOs — Shared DTOs from api-spec.md
# ---------------------------------------------------------------------------


class RunSummaryDto(BaseModel):
    model_config = _CAMEL_CONFIG

    id: uuid.UUID
    agent_ref: str
    status: RunStatus
    stop_reason: StopReason | None = None
    started_at: datetime
    ended_at: datetime | None = None


class LastStepSummary(BaseModel):
    model_config = _CAMEL_CONFIG

    id: uuid.UUID
    step_number: int
    node_name: str
    status: StepStatus


class RunDetailDto(BaseModel):
    model_config = _CAMEL_CONFIG

    id: uuid.UUID
    agent_ref: str
    agent_definition_hash: str
    intake: dict[str, Any]
    status: RunStatus
    stop_reason: StopReason | None = None
    started_at: datetime
    ended_at: datetime | None = None
    trace_uri: str
    step_count: int
    last_step: LastStepSummary | None = None


class StepDto(BaseModel):
    model_config = _CAMEL_CONFIG

    id: uuid.UUID
    step_number: int
    node_name: str
    status: StepStatus
    node_inputs: dict[str, Any]
    node_result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    dispatched_at: datetime | None = None
    completed_at: datetime | None = None


class PolicyCallDto(BaseModel):
    model_config = _CAMEL_CONFIG

    id: uuid.UUID
    step_id: uuid.UUID
    provider: str
    model: str
    selected_tool: str
    tool_arguments: dict[str, Any]
    available_tools: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    latency_ms: int
    created_at: datetime


class WebhookEventDto(BaseModel):
    model_config = _CAMEL_CONFIG

    id: uuid.UUID
    event_type: WebhookEventType
    engine_run_id: str
    payload: dict[str, Any]
    signature_ok: bool
    source: WebhookSource = WebhookSource.ENGINE
    received_at: datetime
    processed_at: datetime | None = None


class EffectorCallDto(BaseModel):
    """Trace entry for one effector fire (FEAT-008)."""

    model_config = _CAMEL_CONFIG

    effector_name: str
    entity_type: Literal["work_item", "task"]
    entity_id: uuid.UUID
    transition: str
    status: Literal["ok", "error", "skipped"]
    duration_ms: int
    error_code: str | None = None
    detail: str | None = None
    emitted_at: datetime


class AgentDto(BaseModel):
    model_config = _CAMEL_CONFIG

    ref: str
    definition_hash: str
    path: str
    intake_schema: dict[str, Any]
    available_nodes: list[str]


# ---------------------------------------------------------------------------
# Request DTOs
# ---------------------------------------------------------------------------


class BudgetConfig(BaseModel):
    model_config = _CAMEL_CONFIG

    max_steps: int | None = None
    max_tokens: int | None = None


class CreateRunRequest(BaseModel):
    model_config = _CAMEL_CONFIG

    agent_ref: str
    intake: dict[str, Any]
    budget: BudgetConfig | None = None


class CancelRunRequest(BaseModel):
    model_config = _CAMEL_CONFIG

    reason: str | None = None


class WebhookEventRequest(BaseModel):
    model_config = _CAMEL_CONFIG

    event_type: WebhookEventType
    engine_run_id: str
    engine_event_id: str
    step_correlation_id: uuid.UUID | None = None
    occurred_at: datetime
    payload: dict[str, Any]


# ---------------------------------------------------------------------------
# Webhook acknowledgement
# ---------------------------------------------------------------------------


class WebhookAckDto(BaseModel):
    model_config = _CAMEL_CONFIG

    received: bool
    event_id: uuid.UUID


# ---------------------------------------------------------------------------
# FEAT-005 — Run signals
# ---------------------------------------------------------------------------


class RunSignalDto(BaseModel):
    """Shape returned to operator clients after a signal is persisted."""

    model_config = _CAMEL_CONFIG

    id: uuid.UUID
    run_id: uuid.UUID
    name: str
    task_id: str | None
    payload: dict[str, Any]
    received_at: datetime
    dedupe_key: str


class SignalCreateRequest(BaseModel):
    """Operator POST body for ``POST /api/v1/runs/{id}/signals``.

    ``name`` is a ``Literal`` in v1 so unknown signal names produce a 422
    (FastAPI auto) before the service layer is reached.
    """

    model_config = _CAMEL_CONFIG

    name: Literal["implementation-complete"]
    task_id: str
    payload: dict[str, Any] = Field(default_factory=dict[str, Any])


class SignalCreateResponse(BaseModel):
    """Envelope returned to operator clients after a signal is persisted."""

    model_config = _CAMEL_CONFIG

    data: RunSignalDto
    meta: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# FEAT-006 — Work items
# ---------------------------------------------------------------------------


class WorkItemDto(BaseModel):
    """Shape returned to clients for a WorkItem row."""

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel, extra="forbid"
    )

    id: uuid.UUID
    external_ref: str
    type: WorkItemType
    title: str
    source_path: str | None = None
    status: WorkItemStatus
    opened_by: str
    closed_at: datetime | None = None
    closed_by: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskAssignmentDto(BaseModel):
    """Shape returned to clients for a TaskAssignment row."""

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel, extra="forbid"
    )

    id: uuid.UUID
    task_id: uuid.UUID
    assignee_type: AssigneeType
    assignee_id: str
    assigned_by: str
    assigned_at: datetime
    superseded_at: datetime | None = None


class TaskDto(BaseModel):
    """Shape returned to clients for a Task row.

    ``current_assignment`` is a computed field populated by the service when
    loading; it is not a DB column on ``tasks``.
    """

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel, extra="forbid"
    )

    id: uuid.UUID
    work_item_id: uuid.UUID
    external_ref: str
    title: str
    status: TaskStatus
    proposer_type: ActorType
    proposer_id: str
    current_assignment: TaskAssignmentDto | None = None
    created_at: datetime
    updated_at: datetime


class ApprovalDto(BaseModel):
    """Shape returned to clients for an Approval row."""

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel, extra="forbid"
    )

    id: uuid.UUID
    task_id: uuid.UUID
    stage: ApprovalStage
    decision: ApprovalDecision
    decided_by: str
    decided_by_role: ActorRole
    feedback: str | None = None
    decided_at: datetime


# ---------------------------------------------------------------------------
# FEAT-006 — Lifecycle signal response envelopes
# ---------------------------------------------------------------------------


class LifecycleSignalMeta(BaseModel):
    """``meta`` payload for lifecycle signal responses."""

    model_config = _CAMEL_CONFIG

    already_received: bool = False


class WorkItemSignalResponse(BaseModel):
    """Response envelope for work-item signal endpoints (S1-S4)."""

    model_config = _CAMEL_CONFIG

    data: WorkItemDto
    meta: LifecycleSignalMeta | None = None


class TaskSignalResponse(BaseModel):
    """Response envelope for task signal endpoints (S5-S14)."""

    model_config = _CAMEL_CONFIG

    data: TaskDto
    meta: LifecycleSignalMeta | None = None


# ---------------------------------------------------------------------------
# FEAT-006 — Request bodies
# ---------------------------------------------------------------------------


class WorkItemCreateRequest(BaseModel):
    """S1 body — open a new work item."""

    model_config = _CAMEL_CONFIG

    external_ref: str
    type: WorkItemType
    title: str
    source_path: str | None = None


class WorkItemLockRequest(BaseModel):
    """S2 body — admin pause.  ``reason`` is optional audit text."""

    model_config = _CAMEL_CONFIG

    reason: str | None = None


class WorkItemUnlockRequest(BaseModel):
    """S3 body — admin resume (empty in v1)."""

    model_config = _CAMEL_CONFIG


class WorkItemCloseRequest(BaseModel):
    """S4 body — admin close.  ``notes`` is optional audit text."""

    model_config = _CAMEL_CONFIG

    notes: str | None = None


class TaskApproveRequest(BaseModel):
    """S5 body — empty."""

    model_config = _CAMEL_CONFIG


class TaskRejectRequest(BaseModel):
    """S6 body — reject proposal with non-empty feedback."""

    model_config = _CAMEL_CONFIG

    feedback: str = Field(min_length=1)


class TaskAssignRequest(BaseModel):
    """S7 body — admin assigns dev or agent."""

    model_config = _CAMEL_CONFIG

    assignee_type: AssigneeType
    assignee_id: str = Field(min_length=1)


class TaskDeferRequest(BaseModel):
    """S14 body — admin defers a non-terminal task."""

    model_config = _CAMEL_CONFIG

    reason: str | None = None


class PlanSubmitRequest(BaseModel):
    """S8 body — submit plan for review."""

    model_config = _CAMEL_CONFIG

    plan_path: str = Field(min_length=1)
    plan_sha: str = Field(min_length=1)


class PlanApproveRequest(BaseModel):
    """S9 body — empty."""

    model_config = _CAMEL_CONFIG


class PlanRejectRequest(BaseModel):
    """S10 body — reject plan with non-empty feedback."""

    model_config = _CAMEL_CONFIG

    feedback: str = Field(min_length=1)


class ImplementationSubmitRequest(BaseModel):
    """S11 (agent path) body — submit implementation for review."""

    model_config = _CAMEL_CONFIG

    pr_url: str | None = None
    commit_sha: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class ReviewApproveRequest(BaseModel):
    """S12 body — empty."""

    model_config = _CAMEL_CONFIG


class ReviewRejectRequest(BaseModel):
    """S13 body — reject review with non-empty feedback."""

    model_config = _CAMEL_CONFIG

    feedback: str = Field(min_length=1)
