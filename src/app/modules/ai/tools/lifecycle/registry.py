"""Local-tool registry for lifecycle-agent tools (FEAT-005 / T-096).

The runtime loop consults :data:`LOCAL_TOOL_HANDLERS` *before* falling
back to engine dispatch.  If the policy's selected tool maps to a local
handler, the runtime runs it in-process against
:class:`LifecycleMemory` — no HTTP, no webhook.

Handlers are async callables with the shape::

    async def handle(
        args: dict[str, Any], *, memory: LifecycleMemory
    ) -> LifecycleMemory | tuple[LifecycleMemory, PauseForSignal]

The return-type split encodes the two flavors: a fresh
:class:`LifecycleMemory` means "step completed"; the tuple form means
"step is in_progress, await a signal."

Keeping this registry in a dedicated module keeps the runtime loop
agent-agnostic — future agents can ship their own registries and wire
them in at the composition root without reshaping ``runtime.py``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.modules.ai.runtime_helpers import PauseForSignal
from app.modules.ai.tools.lifecycle import (
    assign_task,
    close_work_item,
    corrections,
    generate_plan,
    generate_tasks,
    load_work_item,
    review_implementation,
    wait_for_implementation,
)
from app.modules.ai.tools.lifecycle.memory import LifecycleMemory

HandlerResult = LifecycleMemory | tuple[LifecycleMemory, PauseForSignal]
Handler = Callable[..., Awaitable[HandlerResult]]


LOCAL_TOOL_HANDLERS: dict[str, Handler] = {
    load_work_item.TOOL_NAME: load_work_item.handle,
    generate_tasks.TOOL_NAME: generate_tasks.handle,
    assign_task.TOOL_NAME: assign_task.handle,
    generate_plan.TOOL_NAME: generate_plan.handle,
    wait_for_implementation.TOOL_NAME: wait_for_implementation.handle,
    review_implementation.TOOL_NAME: review_implementation.handle,
    corrections.TOOL_NAME: corrections.handle,
    close_work_item.TOOL_NAME: close_work_item.handle,
}


def local_tool_definitions() -> list[Any]:
    """Return :class:`~app.core.llm.ToolDefinition` for every registered handler."""
    return [
        load_work_item.tool_definition(),
        generate_tasks.tool_definition(),
        assign_task.tool_definition(),
        generate_plan.tool_definition(),
        wait_for_implementation.tool_definition(),
        review_implementation.tool_definition(),
        corrections.tool_definition(),
        close_work_item.tool_definition(),
    ]


def is_local_tool(tool_name: str) -> bool:
    return tool_name in LOCAL_TOOL_HANDLERS
