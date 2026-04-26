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


class DispatchState(StrEnum):
    """Lifecycle state of a Dispatch (FEAT-009)."""

    PENDING = "pending"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DispatchMode(StrEnum):
    """Where the executor lives (FEAT-009)."""

    LOCAL = "local"
    REMOTE = "remote"
    HUMAN = "human"


class DispatchOutcome(StrEnum):
    """Terminal outcome of a Dispatch (FEAT-009)."""

    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"


class StopReason(StrEnum):
    """Why a Run terminated."""

    DONE_NODE = "done_node"
    POLICY_TERMINATED = "policy_terminated"
    BUDGET_EXCEEDED = "budget_exceeded"
    ERROR = "error"
    CANCELLED = "cancelled"


class WebhookEventType(StrEnum):
    """Type of inbound webhook event.

    ``github_pr_*`` values were added by FEAT-006 to support GitHub PR
    webhook ingress; ``lifecycle_item_transitioned`` by FEAT-006 rc2 to
    reflect flow-engine lifecycle state changes back to the orchestrator.
    """

    NODE_STARTED = "node_started"
    NODE_FINISHED = "node_finished"
    NODE_FAILED = "node_failed"
    FLOW_TERMINATED = "flow_terminated"
    GITHUB_PR_OPENED = "github_pr_opened"
    GITHUB_PR_CLOSED = "github_pr_closed"
    LIFECYCLE_ITEM_TRANSITIONED = "lifecycle_item_transitioned"
    # FEAT-009 / T-216 — remote executor reported terminal dispatch state
    # via /hooks/executors/{executor_id}.
    EXECUTOR_DISPATCH_RESULT = "executor_dispatch_result"


class WebhookSource(StrEnum):
    """Origin of a WebhookEvent row.

    ``engine`` (default) = HMAC-SHA256 signed by ``ENGINE_WEBHOOK_SECRET``.
    ``github`` = GitHub webhook signature (``X-Hub-Signature-256``) keyed by
    ``GITHUB_WEBHOOK_SECRET``.
    """

    ENGINE = "engine"
    GITHUB = "github"


# ---------------------------------------------------------------------------
# FEAT-006 — Deterministic lifecycle flow
# ---------------------------------------------------------------------------


class WorkItemType(StrEnum):
    """Type of a work item tracked by the orchestrator."""

    FEAT = "FEAT"
    BUG = "BUG"
    IMP = "IMP"


class WorkItemStatus(StrEnum):
    """Lifecycle state of a WorkItem.

    Transitions: ``open -> in_progress <-> locked -> ready -> closed``.
    ``locked`` reachable only from ``in_progress`` in v1.
    """

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    LOCKED = "locked"
    READY = "ready"
    CLOSED = "closed"


class TaskStatus(StrEnum):
    """Lifecycle state of a Task.

    Forward edges: ``proposed -> approved -> assigning -> planning ->
    plan_review -> implementing -> impl_review -> done``.
    Rejection edges: ``plan_review -> planning``, ``impl_review -> implementing``.
    Deferral: any non-terminal -> ``deferred`` (admin only).
    """

    PROPOSED = "proposed"
    APPROVED = "approved"
    ASSIGNING = "assigning"
    PLANNING = "planning"
    PLAN_REVIEW = "plan_review"
    IMPLEMENTING = "implementing"
    IMPL_REVIEW = "impl_review"
    DONE = "done"
    DEFERRED = "deferred"


class ActorType(StrEnum):
    """Type of actor that can propose a task."""

    ADMIN = "admin"
    AGENT = "agent"


class AssigneeType(StrEnum):
    """Type of assignee a task can be assigned to."""

    DEV = "dev"
    AGENT = "agent"


class ActorRole(StrEnum):
    """Role of an actor making an approval/rejection decision.

    Differs from ``ActorType``: approvers are always humans (``admin`` or
    ``dev``), never agents.
    """

    ADMIN = "admin"
    DEV = "dev"


class ApprovalStage(StrEnum):
    """Stage at which an approval decision was recorded."""

    PROPOSED = "proposed"
    PLAN = "plan"
    IMPL = "impl"


class ApprovalDecision(StrEnum):
    """Outcome of an approval decision."""

    APPROVE = "approve"
    REJECT = "reject"
