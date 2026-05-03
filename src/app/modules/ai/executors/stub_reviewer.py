"""Stub-pass reviewer for smoke / CI (IMP-003 / T-270).

Synthesises ``verdict=pass`` for ``review_implementation`` so smoke
runs that lack real PR evidence can still reach ``close_work_item``.
NEVER use in production — every implementation gets rubber-stamped.

Wire-shape: a :class:`LocalExecutor` whose handler builds the same
``result`` dict the LLM path would produce, then runs it through the
shared ``_patch_review`` builder to write the canonical
``lifecycle.v1.reviewHistory`` entry.  Reusing the builder makes drift
between the LLM path and the stub impossible by construction
(BUG-010 contract preserved).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.local import LocalExecutor
from app.modules.ai.models import RunMemory

STUB_FEEDBACK = "stub-pass: smoke / CI shortcut"
STUB_REVIEWER_REF = "local:stub-pass-reviewer"

PatchReviewBuilder = Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]


def make_stub_pass_reviewer(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    patch_review_builder: PatchReviewBuilder,
) -> LocalExecutor:
    """Build a ``LocalExecutor`` that always returns ``verdict=pass``.

    The handler:
      1. Pulls the current task id from ``ctx.intake`` (camelCase or
         snake_case key tolerated for symmetry with the LLM path).
      2. Builds a synthetic ``result`` dict with ``verdict=pass``.
      3. Reads the run's :class:`RunMemory` and runs ``patch_review_builder``
         against the synthetic result + current memory so the
         ``lifecycle.v1.reviewHistory`` entry matches the LLM path's
         shape exactly.
    """

    async def _handler(ctx: DispatchContext) -> Mapping[str, Any]:
        task_id = str(ctx.intake.get("taskId") or ctx.intake.get("task_id") or "")
        result_dict: dict[str, Any] = {
            "task_id": task_id,
            "verdict": "pass",
            "feedback": STUB_FEEDBACK,
        }
        async with session_factory() as session:
            row = await session.scalar(select(RunMemory).where(RunMemory.run_id == ctx.run_id))
        current_memory: Mapping[str, Any] = (row.data if row is not None else {}) or {}
        patch = patch_review_builder(result_dict, current_memory)
        return {**result_dict, "__memory_patch": patch}

    return LocalExecutor(ref=STUB_REVIEWER_REF, handler=_handler)


__all__ = ["STUB_FEEDBACK", "STUB_REVIEWER_REF", "make_stub_pass_reviewer"]
