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
from typing import Any, Literal, cast

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
    verdict: Literal["approve", "reject"] = "approve"
    feedback: str | None = None
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
    verdict: Literal["approve", "reject"] = "approve"
    feedback: str | None = None
    tasks: list[_TaskInput] | None = None

    @field_validator("tasks")
    @classmethod
    def _non_empty_if_present(
        cls, v: list[_TaskInput] | None
    ) -> list[_TaskInput] | None:
        if v is not None and len(v) == 0:
            raise ValueError("tasks list must be non-empty when provided")
        return v


class AssignmentConfirmedPayload(BaseModel):
    model_config = _PayloadConfig
    assignee: str
    task_id: str | None = None

    @field_validator("assignee")
    @classmethod
    def _non_empty_assignee(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("assignee must be non-empty")
        return v


class PlanConfirmedPayload(BaseModel):
    model_config = _PayloadConfig
    verdict: Literal["approve", "reject"] = "approve"
    feedback: str | None = None
    plan: str | None = None


class ReviewCompletedPayload(BaseModel):
    model_config = _PayloadConfig
    verdict: Literal["pass", "fail"]
    feedback: str | None = None


class MockupApprovedPayload(BaseModel):
    """Operator verdict on a generated mockup (FEAT-017).

    ``verdict="reject"`` persists feedback so the next ``generate_mockup``
    invocation can address it.  ``verdict="approve"`` produces an empty
    patch — approval is expressed through the flow routing, not memory.
    """

    model_config = _PayloadConfig
    verdict: Literal["approve", "reject"] = "approve"
    feedback: str | None = None


class ImplementationCompletePayload(BaseModel):
    """Optional metadata the developer submits with ``implementation-complete``.

    All fields are optional — an empty payload is backward-compatible and
    leaves ``implementation_refs`` unchanged.  ``extra="forbid"`` surfaces
    typos as validation errors rather than silent drops (FEAT-016).
    """

    model_config = _PayloadConfig
    pr_url: str | None = None
    commit_sha: str | None = None
    summary: str | None = None


# ---------------------------------------------------------------------------
# Rejection helper (IMP-006)
# ---------------------------------------------------------------------------


def _rejection_patch(
    checkpoint: str,
    feedback: str | None,
    current_memory: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a memory patch that persists rejection feedback.

    Writes to the top-level ``rejections`` sidecar (same pattern as
    ``assignments`` / ``plans``).  Each checkpoint key accumulates an
    attempt counter so prompt context loaders can include the feedback
    on retry.
    """
    existing_raw = current_memory.get("rejections")
    existing: dict[str, Any] = (
        {str(k): v for k, v in cast("dict[str, Any]", existing_raw).items()}
        if isinstance(existing_raw, dict)
        else {}
    )
    merged: dict[str, Any] = dict(existing)
    prior = merged.get(checkpoint)
    prior_attempt: int = (
        int(cast("dict[str, Any]", prior).get("attempt", 0))
        if isinstance(prior, dict)
        else 0
    )
    attempt = prior_attempt + 1
    merged[checkpoint] = {
        "feedback": feedback or "",
        "attempt": attempt,
    }
    return {"rejections": merged}


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

    IMP-006: ``verdict="reject"`` persists feedback to the ``rejections``
    sidecar and skips corrections.  The flow resolver reads the verdict
    from the dispatch result (surfaced by the signal delivery hook) and
    loops back to ``load_work_item``.
    """
    parsed = BriefConfirmedPayload.model_validate(payload)
    if parsed.verdict == "reject":
        return _rejection_patch("confirm_brief", parsed.feedback, current_memory)
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
    if parsed.verdict == "reject":
        return _rejection_patch("confirm_tasks", parsed.feedback, current_memory)
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
    # ``_patch_generate_tasks`` sets ``current_task_id`` to the LLM's
    # first task on generation.  When the operator rewrites the list,
    # the previous ``current_task_id`` may no longer exist in
    # ``memory.tasks`` — downstream nodes (``assign_task``,
    # ``generate_plan``, target_id_resolver lookups) would then fail
    # with "no engine target for this dispatch."  Re-anchor the cursor
    # to the first un-completed task of the replacement list (matches
    # ``mark_task_done``'s loop-back logic).
    completed = set(memory.completed_task_ids)
    memory.current_task_id = next(
        (t.id for t in new_tasks if t.id not in completed),
        None,
    )
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
    if parsed.verdict == "reject":
        return _rejection_patch("confirm_plan", parsed.feedback, current_memory)
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


def apply_assignment_confirmation(
    payload: Mapping[str, Any],
    current_memory: Mapping[str, Any],
) -> dict[str, Any]:
    """Record the operator's chosen assignee for the current task.

    Assignees live at top-level ``assignments[task_id]`` — a sibling of
    ``lifecycle.v1``, NOT nested under it (matches ``apply_plan_correction``'s
    sidecar pattern).  Keeping ``LifecycleTask`` byte-unchanged from v0.3.0
    preserves the "Lifecycle agent variants are peers" contract — v0.3.0
    memory never grows an ``assignments`` key.

    Target task id: ``payload.task_id`` when supplied, else
    ``memory.current_task_id``.  Missing both → ``ValueError``.  Empty /
    whitespace ``assignee`` is rejected at the schema layer.
    """
    parsed = AssignmentConfirmedPayload.model_validate(payload)
    target_task_id = parsed.task_id
    if target_task_id is None:
        memory = read_lifecycle_memory(current_memory)
        target_task_id = memory.current_task_id
    if target_task_id is None:
        raise ValueError(
            "assignment-confirmed received with no resolvable task id — "
            "payload.taskId omitted and lifecycle.v1.current_task_id is None."
        )
    # Merge with the existing ``assignments`` sidecar; without this every
    # per-task loop-back through ``confirm_assignment`` would overwrite prior
    # assignees (the runtime's memory applier replaces top-level keys verbatim).
    existing_any: Any = current_memory.get("assignments") or {}
    if not isinstance(existing_any, dict):
        raise ValueError(
            "existing assignments is not a mapping; refusing to overwrite."
        )
    merged: dict[str, Any] = {
        str(k): v for k, v in existing_any.items()  # type: ignore[union-attr]
    }
    merged[target_task_id] = parsed.assignee
    return {"assignments": merged}


def apply_mockup_approval(
    payload: Mapping[str, Any],
    current_memory: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist mockup rejection feedback or approve with an empty patch (FEAT-017).

    Rejection writes to ``rejections["confirm_mockup"]`` so
    ``_load_mockup_task_context`` in ``bootstrap.py`` can inject the
    feedback into the next ``generate_mockup`` invocation.  Approval
    produces no memory change — the routing verdict is read from the
    dispatch result by the ``checkpoint_approved`` predicate.
    """
    parsed = MockupApprovedPayload.model_validate(payload)
    if parsed.verdict == "reject":
        return _rejection_patch("confirm_mockup", parsed.feedback, current_memory)
    return {}


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


def apply_implementation_signal(
    payload: Mapping[str, Any],
    current_memory: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist the PR URL (and optional metadata) submitted at ``implementation-complete``.

    Writes to the top-level ``implementation_refs[task_id]`` sidecar —
    a sibling of ``plans`` and ``assignments``, keyed by ``current_task_id``
    so multi-task runs accumulate one entry per task without overwriting.

    Empty payload → empty patch (backward compat; existing runs unaffected).
    Missing ``current_task_id`` → empty patch (defensive; shouldn't occur in
    normal flow since the signal fires mid-task).
    """
    parsed = ImplementationCompletePayload.model_validate(payload)
    if not parsed.pr_url and not parsed.commit_sha and not parsed.summary:
        return {}
    memory = read_lifecycle_memory(current_memory)
    task_id = memory.current_task_id
    if not task_id:
        return {}
    existing_raw: Any = current_memory.get("implementation_refs") or {}
    existing: dict[str, Any] = (
        {str(k): v for k, v in cast("dict[str, Any]", existing_raw).items()}
        if isinstance(existing_raw, dict)
        else {}
    )
    existing[task_id] = {
        "prUrl": parsed.pr_url,
        "commitSha": parsed.commit_sha,
        "summary": parsed.summary,
    }
    return {"implementation_refs": existing}


# ---------------------------------------------------------------------------
# Intake builders — enrich the step's ``node_inputs`` so DevHub (or any
# trace consumer) can display the artefact the operator is gating.
#
# Signature: ``(current_memory) -> extra_intake``.  Pure functions — no
# I/O, no session.  Called by the runtime in ``_execute_node`` before
# the Step row is persisted.
# ---------------------------------------------------------------------------


def intake_for_confirm_brief(current_memory: Mapping[str, Any]) -> dict[str, Any]:
    memory = read_lifecycle_memory(current_memory)
    return {
        "workItemSummary": memory.work_item.model_dump(mode="json") if memory.work_item else None,
    }


def intake_for_confirm_tasks(current_memory: Mapping[str, Any]) -> dict[str, Any]:
    memory = read_lifecycle_memory(current_memory)
    return {
        "tasks": [t.model_dump(mode="json") for t in memory.tasks],
    }


def intake_for_confirm_assignment(current_memory: Mapping[str, Any]) -> dict[str, Any]:
    memory = read_lifecycle_memory(current_memory)
    current_task = next(
        (t for t in memory.tasks if t.id == memory.current_task_id), None
    )
    return {
        "currentTask": current_task.model_dump(mode="json") if current_task else None,
        "assignments": dict(current_memory.get("assignments") or {}),
    }


def _resolve_plan_markdown(current_memory: Mapping[str, Any], task_id: str) -> str:
    plans_raw = current_memory.get("plans")
    if not isinstance(plans_raw, dict):
        return ""
    entry_raw = cast("dict[str, Any]", plans_raw).get(task_id)
    if not isinstance(entry_raw, dict):
        return ""
    return str(cast("dict[str, Any]", entry_raw).get("plan_markdown") or "")


def intake_for_confirm_plan(current_memory: Mapping[str, Any]) -> dict[str, Any]:
    memory = read_lifecycle_memory(current_memory)
    task_id = memory.current_task_id or ""
    current_task = next(
        (t for t in memory.tasks if t.id == task_id), None
    )
    return {
        "currentTask": current_task.model_dump(mode="json") if current_task else None,
        "planMarkdown": _resolve_plan_markdown(current_memory, task_id),
    }


def intake_for_confirm_mockup(current_memory: Mapping[str, Any]) -> dict[str, Any]:
    """Surface the generated mockup HTML and description for operator review (FEAT-017)."""
    memory = read_lifecycle_memory(current_memory)
    task_id = memory.current_task_id or ""
    current_task = next(
        (t for t in memory.tasks if t.id == task_id), None
    )
    mockups_raw: Any = current_memory.get("mockups") or {}
    mockup_entry: dict[str, Any] = {}
    if isinstance(mockups_raw, dict):
        raw = cast("dict[str, Any]", mockups_raw).get(task_id)
        if isinstance(raw, dict):
            mockup_entry = cast("dict[str, Any]", raw)
    return {
        "currentTask": current_task.model_dump(mode="json") if current_task else None,
        "mockupHtml": str(mockup_entry.get("mockup_html") or ""),
        "mockupDescription": str(mockup_entry.get("description") or ""),
    }


def intake_for_request_implementation(current_memory: Mapping[str, Any]) -> dict[str, Any]:
    memory = read_lifecycle_memory(current_memory)
    task_id = memory.current_task_id or ""
    current_task = next(
        (t for t in memory.tasks if t.id == task_id), None
    )
    return {
        "currentTask": current_task.model_dump(mode="json") if current_task else None,
        "planMarkdown": _resolve_plan_markdown(current_memory, task_id),
    }


def intake_for_human_review(current_memory: Mapping[str, Any]) -> dict[str, Any]:
    memory = read_lifecycle_memory(current_memory)
    task_id = memory.current_task_id or ""
    current_task = next(
        (t for t in memory.tasks if t.id == task_id), None
    )
    impl_refs_raw: Any = current_memory.get("implementation_refs") or {}
    impl_ref: dict[str, Any] | None = None
    if isinstance(impl_refs_raw, dict):
        impl_ref = cast("dict[str, Any]", impl_refs_raw).get(task_id)
    return {
        "currentTask": current_task.model_dump(mode="json") if current_task else None,
        "planMarkdown": _resolve_plan_markdown(current_memory, task_id),
        "reviewHistory": [r.model_dump(mode="json") for r in memory.review_history if r.task_id == task_id],
        "implementationRef": impl_ref,
    }


__all__ = [
    "AssignmentConfirmedPayload",
    "BriefConfirmedPayload",
    "ImplementationCompletePayload",
    "MockupApprovedPayload",
    "PlanConfirmedPayload",
    "ReviewCompletedPayload",
    "TasksConfirmedPayload",
    "apply_assignment_confirmation",
    "apply_brief_correction",
    "apply_implementation_signal",
    "apply_mockup_approval",
    "apply_plan_correction",
    "apply_review_verdict",
    "apply_tasks_correction",
    "intake_for_confirm_assignment",
    "intake_for_confirm_brief",
    "intake_for_confirm_mockup",
    "intake_for_confirm_plan",
    "intake_for_confirm_tasks",
    "intake_for_human_review",
    "intake_for_request_implementation",
]
