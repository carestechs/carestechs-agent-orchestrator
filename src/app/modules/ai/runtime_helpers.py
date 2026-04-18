"""Pure helpers extracted from the runtime loop (T-039).

Keeps :mod:`app.modules.ai.runtime` focused on loop control flow.  Every
helper here is side-effect-free or documented as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import jsonschema
from jsonschema import ValidationError as JsonSchemaValidationError

from app.core.exceptions import PolicyError
from app.modules.ai.enums import RunStatus, StopReason
from app.modules.ai.tools import TERMINATE_TOOL_NAME

if TYPE_CHECKING:
    from app.core.llm import ToolCall
    from app.modules.ai.agents import AgentDefinition
    from app.modules.ai.models import Run, RunMemory, Step


# ---------------------------------------------------------------------------
# Pause/resume sentinel (FEAT-005 / T-096)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PauseForSignal:
    """Typed signal from a local tool that the runtime must suspend.

    Returning this from a lifecycle tool handler tells the runtime loop:
    (a) persist the step as ``in_progress`` with ``engine_run_id=NULL``;
    (b) do NOT dispatch to the flow engine; (c) await a matching
    :meth:`RunSupervisor.deliver_signal` via ``await_signal``; (d) on
    wake, complete the step with the signal payload as ``node_result``.
    """

    task_id: str
    name: str = "implementation-complete"


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


def merge_memory(current: dict[str, Any], node_result: dict[str, Any] | None) -> dict[str, Any]:
    """Shallow-merge *node_result* into *current*.

    Policy: node results overwrite on leaves; nested dicts are merged
    recursively.  Returning a new dict avoids aliasing the row-bound dict
    across iterations.
    """
    merged: dict[str, Any] = dict(current)
    if not node_result:
        return merged
    for key, value in node_result.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = merge_memory(
                cast("dict[str, Any]", existing),
                cast("dict[str, Any]", value),
            )
        else:
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# Prompt context
# ---------------------------------------------------------------------------


def build_prompt_context(
    run: Run,
    memory: RunMemory,
    last_step: Step | None,
) -> dict[str, Any]:
    """Assemble the structured context the policy receives each iteration."""
    context: dict[str, Any] = {
        "run_id": str(run.id),
        "agent_ref": run.agent_ref,
        "intake": run.intake,
        "memory": memory.data,
    }
    if last_step is not None:
        context["last_step"] = {
            "step_number": last_step.step_number,
            "node_name": last_step.node_name,
            "status": last_step.status,
            "node_result": last_step.node_result,
            "error": last_step.error,
        }
    return context


# ---------------------------------------------------------------------------
# Tool / node resolution
# ---------------------------------------------------------------------------


def tool_call_to_node(tool_call: ToolCall, agent: AgentDefinition) -> str | None:
    """Resolve a policy tool call to a concrete node name.

    Returns ``None`` for the reserved ``terminate`` tool (the loop handles
    termination directly).  Raises :class:`PolicyError` for unknown tools.
    """
    if tool_call.name == TERMINATE_TOOL_NAME:
        return None

    node_names = {node.name for node in agent.nodes}
    if tool_call.name not in node_names:
        raise PolicyError(
            f"policy selected unknown tool: {tool_call.name!r}",
        )
    return tool_call.name


def validate_tool_arguments(tool_call: ToolCall, agent: AgentDefinition) -> None:
    """Validate *tool_call.arguments* against the target node's ``input_schema``.

    Skipped when the node's schema is empty — avoids a second pass through
    ``jsonschema`` for nodes that accept any argument shape.  Raises
    :class:`PolicyError` on validation failure.
    """
    if tool_call.name == TERMINATE_TOOL_NAME:
        return

    node = next((n for n in agent.nodes if n.name == tool_call.name), None)
    if node is None:  # pragma: no cover — caught upstream by tool_call_to_node
        raise PolicyError(f"unknown tool: {tool_call.name!r}")

    if not node.input_schema or not node.input_schema.get("properties"):
        return

    try:
        jsonschema.validate(instance=tool_call.arguments, schema=node.input_schema)
    except JsonSchemaValidationError as exc:
        raise PolicyError(
            f"invalid tool arguments for {tool_call.name!r}: {exc.message}",
        ) from exc


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------


_STOP_TO_RUN_STATUS: dict[StopReason, RunStatus] = {
    StopReason.DONE_NODE: RunStatus.COMPLETED,
    StopReason.POLICY_TERMINATED: RunStatus.COMPLETED,
    StopReason.BUDGET_EXCEEDED: RunStatus.FAILED,
    StopReason.ERROR: RunStatus.FAILED,
    StopReason.CANCELLED: RunStatus.CANCELLED,
}


def run_status_for(reason: StopReason) -> RunStatus:
    """Map a terminal :class:`StopReason` to the corresponding :class:`RunStatus`."""
    return _STOP_TO_RUN_STATUS[reason]
