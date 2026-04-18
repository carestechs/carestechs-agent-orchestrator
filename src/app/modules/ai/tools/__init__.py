"""Policy action space: one tool per agent node plus the built-in terminate tool.

The :func:`build_tools` helper converts an :class:`AgentDefinition` into a
list of :class:`~app.core.llm.ToolDefinition` objects for the policy.
Omitting a node from *available_nodes* hides its tool from the next policy
call — the policy's action space is controlled by iteration, not by prompt.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from app.core.llm import ToolDefinition

if TYPE_CHECKING:
    from app.modules.ai.agents import AgentDefinition

TERMINATE_TOOL_NAME = "terminate"
"""Reserved tool name.  Selecting it ends the run with ``stop_reason=policy_terminated``.

Agent definitions MUST NOT declare a node with this name; the agent loader
enforces that constraint at validation time.
"""

_TERMINATE_TOOL = ToolDefinition(
    name=TERMINATE_TOOL_NAME,
    description="End the run cleanly; no further node dispatches.",
    parameters={"type": "object", "properties": {}},
)


def build_tools(
    agent: AgentDefinition,
    available_nodes: Iterable[str],
) -> list[ToolDefinition]:
    """Return the policy tool list for *agent*, gated by *available_nodes*.

    Order: nodes in the agent's declaration order (filtered by
    ``available_nodes``), then the reserved :data:`TERMINATE_TOOL_NAME`
    appended last.  Unknown names in *available_nodes* are ignored — the
    set is a gate, not a membership declaration.
    """
    available = set(available_nodes)
    tools = [
        ToolDefinition(
            name=node.name,
            description=node.description,
            parameters=node.input_schema,
        )
        for node in agent.nodes
        if node.name in available
    ]
    tools.append(_TERMINATE_TOOL)
    return tools


__all__ = ["TERMINATE_TOOL_NAME", "build_tools"]
