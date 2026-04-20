"""FEAT-006 workflow declarations (rc2 / T-129).

Two workflows registered in the flow engine at orchestrator startup:

- ``work_item_workflow`` — ``open -> in_progress <-> locked -> ready ->
  closed``.
- ``task_workflow`` — nine states with rejection edges at plan/impl
  review and deferral from every non-terminal.

These Python constants are the source of truth passed to the engine's
``POST /api/workflows`` body; the engine stores the definitions after the
first startup and validates every subsequent transition against them.
"""

from __future__ import annotations

from typing import Any

WORK_ITEM_WORKFLOW_NAME = "work_item_workflow"
TASK_WORKFLOW_NAME = "task_workflow"


_WORK_ITEM_TERMINAL_STATUSES = {"closed"}

WORK_ITEM_STATUSES: list[dict[str, Any]] = [
    {
        "name": name,
        "position": pos,
        "isTerminal": name in _WORK_ITEM_TERMINAL_STATUSES,
    }
    for pos, name in enumerate(
        ["open", "in_progress", "locked", "ready", "closed"]
    )
]

WORK_ITEM_TRANSITIONS: list[dict[str, Any]] = [
    {"fromStatus": "open", "toStatus": "in_progress", "name": "approve-first-task"},
    {"fromStatus": "in_progress", "toStatus": "locked", "name": "lock"},
    {"fromStatus": "locked", "toStatus": "in_progress", "name": "unlock"},
    {"fromStatus": "in_progress", "toStatus": "ready", "name": "all-tasks-terminal"},
    {"fromStatus": "ready", "toStatus": "closed", "name": "close"},
]

WORK_ITEM_INITIAL_STATUS = "open"


_TASK_TERMINAL_STATUSES = {"done", "deferred"}

_TASK_STATUS_NAMES = [
    "proposed",
    "approved",
    "assigning",
    "planning",
    "plan_review",
    "implementing",
    "impl_review",
    "done",
    "deferred",
]

TASK_STATUSES: list[dict[str, Any]] = [
    {
        "name": name,
        "position": pos,
        "isTerminal": name in _TASK_TERMINAL_STATUSES,
    }
    for pos, name in enumerate(_TASK_STATUS_NAMES)
]

TASK_TRANSITIONS: list[dict[str, Any]] = [
    # Forward edges
    {"fromStatus": "proposed", "toStatus": "approved", "name": "approve"},
    {"fromStatus": "approved", "toStatus": "assigning", "name": "t4-derived"},
    {"fromStatus": "assigning", "toStatus": "planning", "name": "assign"},
    {"fromStatus": "planning", "toStatus": "plan_review", "name": "submit-plan"},
    {"fromStatus": "plan_review", "toStatus": "implementing", "name": "approve-plan"},
    {"fromStatus": "implementing", "toStatus": "impl_review", "name": "submit-impl"},
    {"fromStatus": "impl_review", "toStatus": "done", "name": "approve-review"},
    # Rejection edges
    {"fromStatus": "plan_review", "toStatus": "planning", "name": "reject-plan"},
    {"fromStatus": "impl_review", "toStatus": "implementing", "name": "reject-review"},
    # Deferral edges: any non-terminal -> deferred
    *[
        {"fromStatus": src, "toStatus": "deferred", "name": "defer"}
        for src in _TASK_STATUS_NAMES
        if src not in _TASK_TERMINAL_STATUSES
    ],
]

TASK_INITIAL_STATUS = "proposed"


ALL_WORKFLOWS: list[dict[str, Any]] = [
    {
        "name": WORK_ITEM_WORKFLOW_NAME,
        "statuses": WORK_ITEM_STATUSES,
        "transitions": WORK_ITEM_TRANSITIONS,
        "initial_status": WORK_ITEM_INITIAL_STATUS,
    },
    {
        "name": TASK_WORKFLOW_NAME,
        "statuses": TASK_STATUSES,
        "transitions": TASK_TRANSITIONS,
        "initial_status": TASK_INITIAL_STATUS,
    },
]
