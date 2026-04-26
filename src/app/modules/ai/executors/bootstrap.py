"""Lifespan-time executor wiring (FEAT-009 / T-214 + T-218).

For every agent loaded from ``agents/`` and every node it declares,
register a concrete :class:`Executor` under
``(agent_ref, node_name)``.  ``v0.1.0`` nodes wrap the existing
``modules/ai/tools/lifecycle/*`` handlers via :class:`LocalExecutor`;
``v0.2.0`` (when it lands in T-222/T-223) registers fresh handlers.

The registration in PR 3 is intentionally minimal: it stands up the
binding so :func:`validate_executor_coverage` can succeed at boot, but
the runtime loop does **not** consume the registry yet — that's the
T-220 loop swap in PR 5.  The local-executor handlers below therefore
raise :class:`NotImplementedError` if invoked, which makes a premature
runtime-loop wiring fail loud rather than silently returning a stub
envelope.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.coverage import (
    ExecutorCoverageError,
    validate_executor_coverage,
)
from app.modules.ai.executors.local import LocalExecutor
from app.modules.ai.executors.registry import ExecutorRegistry

logger = logging.getLogger(__name__)


def register_all_executors(registry: ExecutorRegistry, agents_dir: Path) -> None:
    """Register an executor for every node of every loaded agent.

    The function is the single source of truth for the executor wiring;
    lifespan calls it once at boot and then runs the coverage validator.
    """
    from app.modules.ai.agents import list_agents

    agents = list_agents(agents_dir)
    for agent in agents:
        if agent.ref.startswith("lifecycle-agent@0.1"):
            _register_lifecycle_v01(registry, agent.ref, [n.name for n in agent.nodes])
        elif agent.ref.startswith("lifecycle-agent@0.2"):
            _register_lifecycle_v02(registry, agent.ref)

    logger.info(
        "executor registry: %d binding(s) across %d agent(s)",
        len(registry.registered_keys()),
        len(agents),
    )


def run_coverage_validation(registry: ExecutorRegistry, agents_dir: Path) -> None:
    """Refuse to return when any loaded agent's node is unbound.

    Raises :class:`ExecutorCoverageError` listing every offending
    ``(agent_ref, node_name)`` so an operator can resolve all bootstrap
    gaps in one pass.
    """
    from app.modules.ai.agents import list_agents

    agents = list_agents(agents_dir)
    decls: list[Mapping[str, Any]] = [{"ref": a.ref, "nodes": [{"name": n.name} for n in a.nodes]} for a in agents]
    validate_executor_coverage(registry, decls)


# ---------------------------------------------------------------------------
# v0.1.0 — placeholder handlers
# ---------------------------------------------------------------------------


def _register_lifecycle_v01(registry: ExecutorRegistry, agent_ref: str, node_names: list[str]) -> None:
    """Register a ``LocalExecutor`` for every v0.1.0 lifecycle node.

    The handler raises :class:`NotImplementedError` if invoked — the
    real bridge from ``DispatchContext`` to the existing
    ``modules/ai/tools/lifecycle/*`` ``handle(args, *, memory=...)``
    callables lands with the runtime-loop swap in T-220 (PR 5), which
    is the first caller that actually dispatches through the registry.
    """
    for node_name in node_names:
        executor = LocalExecutor(
            ref=f"local:{node_name}",
            handler=_make_v01_placeholder(agent_ref, node_name),
        )
        registry.register(agent_ref, node_name, executor)


def _make_v01_placeholder(agent_ref: str, node_name: str):  # type: ignore[no-untyped-def]
    """Return a handler that fails loud if invoked before T-220 lands."""

    async def _handler(_ctx: DispatchContext) -> Mapping[str, Any]:
        raise NotImplementedError(
            f"v0.1.0 executor invocation not wired yet "
            f"(agent={agent_ref!r}, node={node_name!r}); "
            "real bridging lands with the T-220 runtime-loop swap (FEAT-009 PR 5)"
        )

    return _handler


# ---------------------------------------------------------------------------
# v0.2.0 — real handlers (FEAT-009 / T-223)
# ---------------------------------------------------------------------------


def _register_lifecycle_v02(registry: ExecutorRegistry, agent_ref: str) -> None:
    """Register the v0.2.0 demo agent's local executors.

    v0.2.0 is a minimal demo proving the new shape end-to-end (dispatch
    verbs + deterministic policy + executor seam).  It is **not** a
    drop-in replacement for v0.1.0 — migrating the full lifecycle (with
    its eight original tools, LifecycleMemory semantics, and the
    wait_for_implementation pause) is tracked as a separate future FEAT.
    """
    registry.register(
        agent_ref,
        "request_work_item_load",
        LocalExecutor(
            ref="local:request_work_item_load",
            handler=_handle_request_work_item_load,
        ),
    )
    registry.register(
        agent_ref,
        "request_closure",
        LocalExecutor(ref="local:request_closure", handler=_handle_request_closure),
    )


async def _handle_request_work_item_load(ctx: DispatchContext) -> Mapping[str, Any]:
    """Load a work-item brief path into the run's memory.

    Pure code; no LLM. The path comes from the run's ``intake.workItemPath``
    forwarded by the runtime via memory bookkeeping (or a future
    enhancement that threads intake into ``DispatchContext.intake``).
    """
    path = ctx.intake.get("workItemPath") or ctx.intake.get("path")
    return {
        "loaded": True,
        "path": str(path) if path is not None else None,
        "__memory_patch": {"work_item_path": str(path) if path is not None else None},
    }


async def _handle_request_closure(_ctx: DispatchContext) -> Mapping[str, Any]:
    """Mark closure (terminal). Pure code; no LLM."""
    return {"closed": True}


__all__ = [
    "ExecutorCoverageError",
    "register_all_executors",
    "run_coverage_validation",
]
