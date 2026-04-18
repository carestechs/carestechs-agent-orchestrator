"""Lifecycle tool: ``wait_for_implementation`` (FEAT-005 / T-096).

Unlike the other lifecycle tools, this one does NOT execute the stage's
effect locally.  It returns a :class:`PauseForSignal` sentinel that tells
the runtime: persist the step as ``in_progress``, skip the engine
dispatch, and await a matching operator signal via
``supervisor.await_signal``.  The runtime then completes the step with
the signal payload as its ``node_result``.
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import PolicyError
from app.core.llm import ToolDefinition
from app.modules.ai.runtime_helpers import PauseForSignal
from app.modules.ai.tools.lifecycle.memory import LifecycleMemory

TOOL_NAME = "wait_for_implementation"


def tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name=TOOL_NAME,
        description=(
            "Pause the run and wait for an operator-injected "
            "'implementation-complete' signal for the given task.  The run "
            "resumes when POST /api/v1/runs/{id}/signals delivers the signal."
        ),
        parameters={
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    )


async def handle(
    args: dict[str, Any],
    *,
    memory: LifecycleMemory,
) -> tuple[LifecycleMemory, PauseForSignal]:
    task_id: str = args["task_id"]
    if not any(t.id == task_id for t in memory.tasks):
        raise PolicyError(f"unknown task: {task_id}")

    new_memory = memory.model_copy(update={"current_task_id": task_id})
    return new_memory, PauseForSignal(task_id=task_id)
