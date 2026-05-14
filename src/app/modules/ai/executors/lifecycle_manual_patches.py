"""Memory-patch builders for ``lifecycle-agent@0.4.0-manual`` (FEAT-015 / T-297).

Each function consumes a ``(signal_payload, current_memory)`` pair and
returns a patch dict the runtime merges into ``RunMemory.data`` via the
``__memory_patch`` hook on the dispatch envelope's result (see
``runtime_deterministic.py:_write_state``).

These are pure functions: no I/O, no session, no LLM access.  The
:class:`HumanExecutor` (T-296) loads the current memory from the DB
once at signal-delivery time and passes the data dict in.

Shape parity:

- ``apply_brief_correction``: writes ``lifecycle.v1.work_item`` via
  the full namespace blob (matches ``_patch_review``'s shape).
- ``apply_tasks_correction``: same, writes ``lifecycle.v1.tasks``.
- ``apply_plan_correction``: writes the top-level ``plans[task_id]``
  sidecar (matches ``_patch_generate_plan``'s shape — the engine plan
  store is a sibling of ``lifecycle.v1``, not nested under it).
- ``apply_review_verdict``: delegates to the existing
  :func:`_patch_review` in ``bootstrap.py`` — the LLM reviewer's writer
  is the parity anchor, so the human variant cannot drift the shape.

Builders MUST NOT return keys starting with ``_``: the runtime's
``_write_state`` filters those.  Validation errors raise
``pydantic.ValidationError``; business-rule violations raise
``ValueError``.  Both surface as a failed dispatch in T-296's hook,
which terminates the run with ``stop_reason=error``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from app.modules.ai.tools.lifecycle.memory import (
    LIFECYCLE_MEMORY_NS,
    LifecycleTask,
    WorkItemRef,
    read_lifecycle_memory,
    to_run_memory,
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


_PayloadConfig = ConfigDict(
    populate_by_name=True,
    alias_generator=to_camel,
    extra="forbid",
)
"""Strict camelCase for operator-delivered payloads.

``extra="forbid"`` makes typos surface as 400s rather than silent no-ops.
"""


class _WorkItemCorrection(BaseModel):
    model_config = _PayloadConfig
    title: str | None = None
    type: Literal["FEAT", "BUG", "IMP"] | None = None


class BriefConfirmedPayload(BaseModel):
    model_config = _PayloadConfig
    work_item: _WorkItemCorrection | None = None


class _TaskInput(BaseModel):
    """Minimum-viable task shape an operator can deliver.

    Required: ``id`` + ``title``.  Everything else carries over from the
    pre-existing memory entry (when ``id`` matches an LLM-generated task)
    or falls back to ``LifecycleTask`` defaults (when the operator is
    introducing a brand-new task).
    """

    model_config = _PayloadConfig
    id: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1)
    summary: str | None = None
    description: str | None = None
    complexity: Literal["small", "medium", "large"] | None = None


class TasksConfirmedPayload(BaseModel):
    model_config = _PayloadConfig
    tasks: list[_TaskInput] | None = None

    @field_validator("tasks")
    @classmethod
    def _non_empty_if_present(
        cls, v: list[_TaskInput] | None
    ) -> list[_TaskInput] | None:
        if v is not None and len(v) == 0:
            raise ValueError("tasks list must be non-empty when provided")
        return v


class PlanConfirmedPayload(BaseModel):
    model_config = _PayloadConfig
    plan: str | None = None


class ReviewCompletedPayload(BaseModel):
    model_config = _PayloadConfig
    verdict: Literal["pass", "fail"]
    feedback: str | None = None


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def apply_brief_correction(
    payload: Mapping[str, Any],
    current_memory: Mapping[str, Any],
) -> dict[str, Any]:
    """Optional title / type overrides for the LLM-derived brief.

    Empty payload → empty patch (operator approves unchanged).  Missing
    ``work_item`` in memory → ``ValueError`` (operator delivered too
    early; ``load_work_item`` hasn't populated memory yet).
    """
    parsed = BriefConfirmedPayload.model_validate(payload)
    if parsed.work_item is None or (
        parsed.work_item.title is None and parsed.work_item.type is None
    ):
        return {}
    memory = read_lifecycle_memory(current_memory)
    if memory.work_item is None:
        raise ValueError(
            "brief-confirmed received but lifecycle.v1.work_item is not yet "
            "populated — wait for load_work_item to complete."
        )
    merged_title = parsed.work_item.title or memory.work_item.title
    merged_type = parsed.work_item.type or memory.work_item.type
    memory.work_item = WorkItemRef(
        id=memory.work_item.id,
        type=merged_type,
        title=merged_title,
        path=memory.work_item.path,
    )
    return {LIFECYCLE_MEMORY_NS: to_run_memory(memory)}


def apply_tasks_correction(
    payload: Mapping[str, Any],
    current_memory: Mapping[str, Any],
) -> dict[str, Any]:
    """Replace ``lifecycle.v1.tasks`` with the operator-curated list.

    Where an operator-supplied task id matches a pre-existing LLM task,
    optional fields (``description``, ``acceptance_criteria``,
    ``complexity``, etc.) carry over so the operator doesn't lose them
    by replacing.  New task ids (operator-introduced) use
    :class:`LifecycleTask` defaults.

    Empty payload → empty patch.  ``tasks=[]`` → ``ValidationError``
    (rejected at the schema layer per FEAT-015 §9).
    """
    parsed = TasksConfirmedPayload.model_validate(payload)
    if parsed.tasks is None:
        return {}
    memory = read_lifecycle_memory(current_memory)
    by_id: dict[str, LifecycleTask] = {t.id: t for t in memory.tasks}

    new_tasks: list[LifecycleTask] = []
    for entry in parsed.tasks:
        existing = by_id.get(entry.id)
        if existing is not None:
            # Carry over the LLM-generated body; override only what the
            # operator explicitly provided.
            new_tasks.append(
                existing.model_copy(
                    update={
                        "title": entry.title,
                        "description": entry.description or existing.description,
                        "complexity": entry.complexity or existing.complexity,
                    }
                )
            )
        else:
            new_tasks.append(
                LifecycleTask(
                    id=entry.id,
                    title=entry.title,
                    description=entry.description or entry.summary or "",
                    complexity=entry.complexity or "medium",
                )
            )
    memory.tasks = new_tasks
    return {LIFECYCLE_MEMORY_NS: to_run_memory(memory)}


def apply_plan_correction(
    payload: Mapping[str, Any],
    current_memory: Mapping[str, Any],
) -> dict[str, Any]:
    """Replace the plan markdown for the current task.

    Plans live at top-level ``plans[task_id]`` — a sibling of
    ``lifecycle.v1``, NOT nested under it (matches
    ``_patch_generate_plan`` in ``bootstrap.py``).  Empty payload →
    empty patch.  Missing ``current_task_id`` → ``ValueError``.
    """
    parsed = PlanConfirmedPayload.model_validate(payload)
    if parsed.plan is None:
        return {}
    memory = read_lifecycle_memory(current_memory)
    if memory.current_task_id is None:
        raise ValueError(
            "plan-confirmed received with no current_task_id in lifecycle memory — "
            "wait for assign_task / generate_plan to set it."
        )
    # Merge with the existing ``plans`` sidecar; without this every per-task
    # iteration would overwrite prior plans (the runtime's memory applier
    # replaces top-level keys verbatim).
    existing_plans_any: Any = current_memory.get("plans") or {}
    if not isinstance(existing_plans_any, dict):
        existing_plans_any = {}
    merged_plans: dict[str, Any] = {
        str(k): v for k, v in existing_plans_any.items()  # type: ignore[union-attr]
    }
    merged_plans[memory.current_task_id] = {"plan_markdown": parsed.plan}
    return {"plans": merged_plans}


def apply_review_verdict(
    payload: Mapping[str, Any],
    current_memory: Mapping[str, Any],
) -> dict[str, Any]:
    """Append a review history entry mirroring the LLM reviewer's shape.

    Delegates the patch construction to :func:`_patch_review` (defined
    in ``executors/bootstrap.py``) so the human reviewer cannot drift
    from the LLM reviewer's contract.  The ``review_passed`` predicate +
    ``correct_implementation`` node read the same field structure from
    either writer.
    """
    parsed = ReviewCompletedPayload.model_validate(payload)
    memory = read_lifecycle_memory(current_memory)
    if memory.current_task_id is None:
        raise ValueError(
            "review-completed received with no current_task_id in lifecycle memory."
        )
    # Lazy import to avoid a circular dependency at module-load time —
    # ``bootstrap.py`` imports this module from inside
    # ``register_lifecycle_v04_manual``.  ``_patch_review`` was lifted
    # to module scope in IMP-003 / T-270 explicitly so cross-module
    # callers (stub-pass reviewer, this builder) could share the exact
    # shape contract; the leading underscore is a holdover.
    from app.modules.ai.executors import bootstrap as _bootstrap

    return _bootstrap._patch_review(  # pyright: ignore[reportPrivateUsage]
        {
            "task_id": memory.current_task_id,
            "verdict": parsed.verdict,
            "feedback": parsed.feedback or "",
        },
        current_memory,
    )


__all__ = [
    "BriefConfirmedPayload",
    "PlanConfirmedPayload",
    "ReviewCompletedPayload",
    "TasksConfirmedPayload",
    "apply_brief_correction",
    "apply_plan_correction",
    "apply_review_verdict",
    "apply_tasks_correction",
]
