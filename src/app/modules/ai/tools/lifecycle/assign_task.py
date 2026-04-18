"""Lifecycle tool: ``assign_task`` (FEAT-005 / T-092).

Deterministic-v1 tool.  Every task gets ``executor='local-claude-code'``.
Its only reason for existing in v1 is to make the stage visible in the
trace — future multi-executor routing plugs in here.
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import PolicyError
from app.core.llm import ToolDefinition
from app.modules.ai.tools.lifecycle.memory import LifecycleMemory

TOOL_NAME = "assign_task"
DEFAULT_EXECUTOR = "local-claude-code"


def tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name=TOOL_NAME,
        description=(
            "Assign an executor to a task.  v1 always picks 'local-claude-code'; "
            "the tool exists so the assignment is visible in the trace."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    )


async def handle(args: dict[str, Any], *, memory: LifecycleMemory) -> LifecycleMemory:
    task_id: str = args["task_id"]
    tasks = list(memory.tasks)
    for i, task in enumerate(tasks):
        if task.id == task_id:
            tasks[i] = task.model_copy(update={"executor": DEFAULT_EXECUTOR})
            return memory.model_copy(update={"tasks": tasks})
    raise PolicyError(f"unknown task: {task_id}")
