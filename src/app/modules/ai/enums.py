"""Shared string enums for the AI module — single source of truth for models and schemas."""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    """Lifecycle status of a Run."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    """Lifecycle status of a Step."""

    PENDING = "pending"
    DISPATCHED = "dispatched"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class StopReason(StrEnum):
    """Why a Run terminated."""

    DONE_NODE = "done_node"
    POLICY_TERMINATED = "policy_terminated"
    BUDGET_EXCEEDED = "budget_exceeded"
    ERROR = "error"
    CANCELLED = "cancelled"


class WebhookEventType(StrEnum):
    """Type of inbound engine webhook event."""

    NODE_STARTED = "node_started"
    NODE_FINISHED = "node_finished"
    NODE_FAILED = "node_failed"
    FLOW_TERMINATED = "flow_terminated"
