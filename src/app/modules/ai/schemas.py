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

from app.modules.ai.enums import RunStatus, StepStatus, StopReason, WebhookEventType

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
    received_at: datetime
    processed_at: datetime | None = None


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
