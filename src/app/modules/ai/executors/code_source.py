"""Accessor for the run-intake ``codeSource`` block (IMP-005).

This is the sole sanctioned read path for executors that need a
``(repo, baseBranch, workBranch?)`` handle.  Executors must NOT call
``ctx.intake["codeSource"]`` directly — doing so bypasses the
memory-sidecar precedence and is a review blocker.

Read precedence for ``workBranch``: memory sidecar (when non-empty) →
intake.  Operator-supplied intake ``workBranch`` is authoritative when
memory carries no value; the producer executor that fills the sidecar
must only do so when intake omits the field.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.schemas import CodeSourceDto


def read_code_source(
    ctx: DispatchContext,
    *,
    memory: Mapping[str, Any] | None = None,
) -> CodeSourceDto:
    """Resolve the run's code-source block from intake + optional memory.

    Raises ``ValueError`` if intake carries no ``codeSource`` key.  Shape
    errors raise ``pydantic.ValidationError`` (intake is validated lazily
    here — strict shape gating lives at the route via the
    ``LIFECYCLE_CODE_SOURCE_REQUIRED`` setting).
    """
    raw = ctx.intake.get("codeSource")
    if raw is None:
        raise ValueError("codeSource missing from intake")

    dto = CodeSourceDto.model_validate(raw)

    if memory is not None:
        sidecar = memory.get("codeSource") or {}
        if isinstance(sidecar, Mapping):
            memory_work_branch = sidecar.get("workBranch")
            if memory_work_branch:
                dto = dto.model_copy(update={"work_branch": memory_work_branch})

    return dto


__all__ = ["read_code_source"]
