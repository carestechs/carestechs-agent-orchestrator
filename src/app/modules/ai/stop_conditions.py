"""Stop-condition rules for the runtime loop (T-033).

Pure, side-effect-free functions each return a :class:`StopReason` when the
rule matches, else ``None``.  :func:`evaluate` applies them in a documented
priority order so the loop can stop on the first match without having to
think about which rule wins.

Priority (first match wins):

1. ``CANCELLED`` — user intent should never be masked by a concurrent failure.
2. ``ERROR`` — a policy / engine / validation error terminates immediately.
3. ``BUDGET_EXCEEDED`` — step / token budgets.
4. ``POLICY_TERMINATED`` — the policy selected the reserved ``terminate`` tool.
5. ``DONE_NODE`` — the policy selected a terminal-node tool.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.modules.ai.enums import StopReason
from app.modules.ai.tools import TERMINATE_TOOL_NAME

if TYPE_CHECKING:
    from app.core.llm import ToolCall


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """Snapshot of runtime state evaluated at each loop iteration."""

    last_tool: ToolCall | None
    step_count: int
    token_count: int
    max_steps: int | None
    max_tokens: int | None
    last_policy_error: Exception | None
    last_engine_error: Exception | None
    cancel_requested: bool
    terminal_nodes: frozenset[str]
    # FEAT-005 / T-097 — correction-attempt bound for lifecycle agents.
    # ``correction_attempts`` is a snapshot of per-task retry counts; with
    # ``max_corrections`` set, any task exceeding the cap trips ``ERROR``.
    # Agents that don't track corrections leave both fields at defaults.
    correction_attempts: dict[str, int] | None = None
    max_corrections: int | None = None


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def is_cancelled(s: RuntimeState) -> StopReason | None:
    """Returns ``CANCELLED`` iff cancellation was requested."""
    return StopReason.CANCELLED if s.cancel_requested else None


def is_error(s: RuntimeState) -> StopReason | None:
    """Returns ``ERROR`` iff the previous iteration recorded a policy or engine error."""
    if s.last_policy_error is not None or s.last_engine_error is not None:
        return StopReason.ERROR
    return None


def correction_budget_exceeded(s: RuntimeState) -> StopReason | None:
    """Returns ``ERROR`` iff any task's correction attempts exceed ``max_corrections``.

    A lifecycle-agent bound (FEAT-005 / T-097).  Agents that don't set
    ``max_corrections`` or don't track corrections are unaffected.
    """
    if s.max_corrections is None or not s.correction_attempts:
        return None
    for _task_id, attempts in s.correction_attempts.items():
        if attempts > s.max_corrections:
            return StopReason.ERROR
    return None


def find_correction_exceedance(
    s: RuntimeState,
) -> tuple[str, int] | None:
    """Return ``(task_id, attempts)`` of the first task exceeding the bound, else ``None``.

    Companion to :func:`correction_budget_exceeded` — callers that want to
    build a rich ``final_state`` use this to surface which task tripped.
    """
    if s.max_corrections is None or not s.correction_attempts:
        return None
    for task_id, attempts in s.correction_attempts.items():
        if attempts > s.max_corrections:
            return task_id, attempts
    return None


def is_budget_exceeded(s: RuntimeState) -> StopReason | None:
    """Returns ``BUDGET_EXCEEDED`` iff step or token budget is reached."""
    if s.max_steps is not None and s.step_count >= s.max_steps:
        return StopReason.BUDGET_EXCEEDED
    if s.max_tokens is not None and s.token_count >= s.max_tokens:
        return StopReason.BUDGET_EXCEEDED
    return None


def is_policy_terminated(s: RuntimeState) -> StopReason | None:
    """Returns ``POLICY_TERMINATED`` iff the policy chose the reserved ``terminate`` tool."""
    if s.last_tool is not None and s.last_tool.name == TERMINATE_TOOL_NAME:
        return StopReason.POLICY_TERMINATED
    return None


def is_done_node(s: RuntimeState) -> StopReason | None:
    """Returns ``DONE_NODE`` iff the last tool selection matched a terminal node."""
    if s.last_tool is not None and s.last_tool.name in s.terminal_nodes:
        return StopReason.DONE_NODE
    return None


# ---------------------------------------------------------------------------
# Evaluation order
# ---------------------------------------------------------------------------

_PRIORITY: tuple[Callable[[RuntimeState], StopReason | None], ...] = (
    is_cancelled,
    is_error,
    correction_budget_exceeded,
    is_budget_exceeded,
    is_policy_terminated,
    is_done_node,
)


def evaluate(state: RuntimeState) -> StopReason | None:
    """Return the first matching :class:`StopReason`, or ``None`` if the loop should continue."""
    for rule in _PRIORITY:
        reason = rule(state)
        if reason is not None:
            return reason
    return None
