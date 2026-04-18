"""Lifecycle tool: ``corrections`` (FEAT-005 / T-097).

A marker stage the policy selects to route back to ``implementation`` after
a failed review.  The handler increments
``memory.correction_attempts[task_id]`` — the runtime's correction-bound
stop condition (:func:`stop_conditions.correction_budget_exceeded`)
reads this field to decide when to terminate the run.
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import PolicyError
from app.core.llm import ToolDefinition
from app.modules.ai.tools.lifecycle.memory import LifecycleMemory

TOOL_NAME = "corrections"


def tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name=TOOL_NAME,
        description=(
            "Route back to implementation after a failed review.  Increments "
            "memory.correction_attempts[task_id]; the run's correction bound "
            "terminates the loop if the count exceeds LIFECYCLE_MAX_CORRECTIONS."
        ),
        parameters={
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    )


async def handle(args: dict[str, Any], *, memory: LifecycleMemory) -> LifecycleMemory:
    task_id: str = args["task_id"]
    if not any(t.id == task_id for t in memory.tasks):
        raise PolicyError(f"unknown task: {task_id}")

    attempts = dict(memory.correction_attempts)
    attempts[task_id] = attempts.get(task_id, 0) + 1
    return memory.model_copy(update={"correction_attempts": attempts})
