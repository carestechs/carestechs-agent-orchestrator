"""Executor seam (FEAT-009).

The runtime loop dispatches every artifact-producing step to a registered
``Executor`` — local, remote, or human.  This package owns the seam:

* :mod:`base` — the ``Executor`` Protocol and the ``DispatchContext``
  passed into every dispatch.
* :mod:`binding` — ``ExecutorBinding`` and the ``no_executor`` exemption
  helper for nodes that intentionally bypass the registry.
* :mod:`registry` — the in-process ``ExecutorRegistry`` keyed on
  ``(agent_ref, node_name)``.
* :mod:`coverage` — the lifespan-time validator that refuses to boot
  when an agent node has neither a registration nor an exemption.

Concrete adapters (``LocalExecutor``, ``RemoteExecutor``, ``HumanExecutor``)
land in T-214/T-215/T-217 alongside the bootstrap module that wires them.
"""

from __future__ import annotations

from app.modules.ai.executors.base import (
    DispatchContext,
    Executor,
    ExecutorMode,
)
from app.modules.ai.executors.binding import (
    ExecutorBinding,
    iter_no_executor_exemptions,
    no_executor,
)
from app.modules.ai.executors.coverage import (
    ExecutorCoverageError,
    validate_executor_coverage,
)
from app.modules.ai.executors.registry import ExecutorRegistry

__all__ = [
    "DispatchContext",
    "Executor",
    "ExecutorBinding",
    "ExecutorCoverageError",
    "ExecutorMode",
    "ExecutorRegistry",
    "iter_no_executor_exemptions",
    "no_executor",
    "validate_executor_coverage",
]
