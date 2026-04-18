"""Webhook → step state-machine reconciliation (T-038).

Pure function + service helpers that translate an inbound
:class:`~app.modules.ai.models.WebhookEvent` into a ``Step`` status update
and wake the run-loop coroutine.

Step transitions are **monotonic** — a late ``NODE_STARTED`` arriving after
``NODE_FINISHED`` is ignored rather than rolling the state backwards.
"""

from __future__ import annotations

from app.modules.ai.enums import StepStatus, WebhookEventType

# ---------------------------------------------------------------------------
# Transition ordering (monotonic rank)
# ---------------------------------------------------------------------------

_STATUS_RANK: dict[StepStatus, int] = {
    StepStatus.PENDING: 0,
    StepStatus.DISPATCHED: 1,
    StepStatus.IN_PROGRESS: 2,
    StepStatus.COMPLETED: 3,
    StepStatus.FAILED: 3,
}

# ---------------------------------------------------------------------------
# Event-type → target status
# ---------------------------------------------------------------------------

_EVENT_TARGETS: dict[WebhookEventType, StepStatus | None] = {
    WebhookEventType.NODE_STARTED: StepStatus.IN_PROGRESS,
    WebhookEventType.NODE_FINISHED: StepStatus.COMPLETED,
    WebhookEventType.NODE_FAILED: StepStatus.FAILED,
    # Run-level event — no step-level transition.
    WebhookEventType.FLOW_TERMINATED: None,
}


def next_step_state(
    current: StepStatus, event_type: WebhookEventType
) -> tuple[StepStatus, bool]:
    """Return ``(new_status, changed)`` for *current* under *event_type*.

    Transitions are monotonic on :data:`_STATUS_RANK`: a later event can only
    move a step forward.  If the event does not apply (e.g. ``FLOW_TERMINATED``
    for a step-level state machine) returns ``(current, False)``.
    """
    target = _EVENT_TARGETS.get(event_type)
    if target is None:
        return current, False

    if _STATUS_RANK[target] <= _STATUS_RANK[current]:
        return current, False

    return target, True
