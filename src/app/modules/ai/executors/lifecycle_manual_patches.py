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
    id: str | None = None
    title: str | None = None
    type: Literal["FEAT", "BUG", "IMP"] | None = None
    # FEAT-018: full-authoring fields for @0.6.0-human (ignored in prior
    # variants when work_item is already populated from load_work_item).
    summary: str | None = None
    acceptance_criteria: list[str] | None = None


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
    # FEAT-018: kind field for @0.6.0-human; None falls back to LifecycleTask default "feature".
    kind: Literal["feature", "mockup", "bug", "chore"] | None = None
    # FEAT-019: explicit workflow hint; None → auto-derive at render time.
    workflow: Literal["standard", "mockup-first", "investigation-first"] | None = None
    # FEAT-019: sibling task IDs this task depends on (maps to LifecycleTask.depends_on).
    dependencies: list[str] = Field(default_factory=list)
    # FEAT-019: file paths expected to change (maps to LifecycleTask.files_hint).
    files_to_modify: list[str] = Field(default_factory=list)


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
    """Operator verdict on a generated mockup (FEAT-017 / FEAT-018).

    ``verdict="reject"`` persists feedback so the next ``generate_mockup``
    invocation can address it.  ``verdict="approve"`` produces an empty
    patch in @0.5.0-manual (the LLM already wrote the HTML in generate_mockup).

    FEAT-018 (@0.6.0-human): no generate_mockup runs, so the operator
    supplies ``mockupHtml`` directly on approve; the patch builder writes
    it to ``RunMemory.mockups[task_id]``.  Field is ignored on reject.
    """

    model_config = _PayloadConfig
    verdict: Literal["approve", "reject"] = "approve"
    feedback: str | None = None
    mockup_html: str | None = None


class TasksReviewedPayload(BaseModel):
    """Operator verdict at the ``confirm_task_review`` checkpoint (FEAT-019).

    Independent reviewer signs off (or requests revisions) on the authored
    task list before it is fanned out to the engine.  ``verdict="approve"``
    advances to ``propose_tasks``; ``verdict="reject"`` loops back to
    ``confirm_tasks`` with optional reviewer feedback surfaced as
    ``rejections["confirm_task_review"]`` in memory.
    """

    model_config = _PayloadConfig
    verdict: Literal["approve", "reject"] = "approve"
    feedback: str | None = None


class DocsUpdateConfirmedPayload(BaseModel):
    """Operator confirmation at the ``confirm_docs_update`` checkpoint (FEAT-019).

    Gate before ``close_work_item``.  ``verdict="approve"`` advances to
    closure; ``verdict="reject"`` holds at the checkpoint for a re-check.
    Optional ``notes`` are stored in the rejection sidecar.
    """

    model_config = _PayloadConfig
    verdict: Literal["approve", "reject"] = "approve"
    feedback: str | None = None


class TestsCompletedPayload(BaseModel):
    """Operator-reported test result at the ``run_tests`` human checkpoint (FEAT-020).

    The operator runs the project's test suite manually and pastes the outcome
    here.  ``passed`` drives the ``testResult`` shown to the reviewer in
    ``human_review_implementation`` nodeInputs.  ``output`` is the raw console
    output (pytest summary, etc.) truncated to a reasonable length.
    """

    model_config = _PayloadConfig
    passed: bool
    output: str = ""


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
    """Title / type / summary overrides for an LLM-derived brief, or full
    authoring for ``@0.6.0-human`` where no prior ``load_work_item`` ran.

    Modes:
    - ``verdict="reject"`` → rejection patch; skips corrections.
    - ``verdict="approve"`` + no ``work_item`` in payload → empty patch.
    - ``verdict="approve"`` + ``work_item`` + memory already has work_item
      → merge/override (prior variants @0.4/0.5-manual).
    - ``verdict="approve"`` + ``work_item.id`` in payload + memory empty
      → create WorkItemRef from scratch (@0.6.0-human).
    """
    parsed = BriefConfirmedPayload.model_validate(payload)
    if parsed.verdict == "reject":
        return _rejection_patch("confirm_brief", parsed.feedback, current_memory)
    wi = parsed.work_item
    if wi is None:
        return {}
    memory = read_lifecycle_memory(current_memory)
    if memory.work_item is not None:
        # Prior variants: merge operator corrections on top of LLM draft.
        # When all override fields are absent, nothing changed — skip the patch.
        if (
            wi.title is None
            and wi.type is None
            and wi.summary is None
            and wi.acceptance_criteria is None
        ):
            return {}
        merged_title = wi.title or memory.work_item.title
        merged_type = wi.type or memory.work_item.type
        memory.work_item = WorkItemRef(
            id=memory.work_item.id,
            type=merged_type,
            title=merged_title,
            path=memory.work_item.path,
            summary=wi.summary if wi.summary is not None else memory.work_item.summary,
            acceptance_criteria=(
                wi.acceptance_criteria
                if wi.acceptance_criteria is not None
                else memory.work_item.acceptance_criteria
            ),
        )
    else:
        # @0.6.0-human: operator authors the brief from scratch.
        if not wi.id or not wi.title or not wi.type:
            raise ValueError(
                "brief-confirmed for a run without a prior load_work_item step "
                "requires workItem.id, workItem.title, and workItem.type in the payload."
            )
        memory.work_item = WorkItemRef(
            id=wi.id,
            type=wi.type,
            title=wi.title,
            path="",
            summary=wi.summary or "",
            acceptance_criteria=wi.acceptance_criteria or [],
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
                        "kind": entry.kind or existing.kind,
                        "workflow": entry.workflow if entry.workflow is not None else existing.workflow,
                        "depends_on": entry.dependencies if entry.dependencies else existing.depends_on,
                        "files_hint": entry.files_to_modify if entry.files_to_modify else existing.files_hint,
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
                    kind=entry.kind or "feature",
                    workflow=entry.workflow,
                    depends_on=entry.dependencies,
                    files_hint=entry.files_to_modify,
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
    """Persist mockup rejection feedback or write operator-supplied HTML on approve.

    Rejection writes to ``rejections["confirm_mockup"]`` so
    ``_load_mockup_task_context`` in ``bootstrap.py`` can inject the
    feedback into the next ``generate_mockup`` invocation.

    Approval in @0.5.0-manual: empty patch — the HTML was already written
    by ``generate_mockup``'s LLMContentExecutor.

    Approval in @0.6.0-human (FEAT-018): ``mockupHtml`` in the payload is
    written to ``RunMemory.mockups[task_id]`` so downstream nodes (plan,
    implementation) see the same memory shape as the automated path.
    """
    parsed = MockupApprovedPayload.model_validate(payload)
    if parsed.verdict == "reject":
        return _rejection_patch("confirm_mockup", parsed.feedback, current_memory)
    if not parsed.mockup_html:
        return {}
    # Operator supplied HTML — write it to the mockups sidecar.
    memory = read_lifecycle_memory(current_memory)
    task_id = memory.current_task_id or ""
    if not task_id:
        return {}
    existing_raw: Any = current_memory.get("mockups") or {}
    existing: dict[str, Any] = (
        {str(k): v for k, v in cast("dict[str, Any]", existing_raw).items()}
        if isinstance(existing_raw, dict)
        else {}
    )
    existing[task_id] = {
        "mockup_html": parsed.mockup_html,
        "description": "",
    }
    return {"mockups": existing}


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
    test_result: Any = None
    test_results_raw: Any = current_memory.get("testResults") or {}
    if isinstance(test_results_raw, dict) and task_id:
        test_result = cast("dict[str, Any]", test_results_raw).get(task_id)
    return {
        "currentTask": current_task.model_dump(mode="json") if current_task else None,
        "planMarkdown": _resolve_plan_markdown(current_memory, task_id),
        "reviewHistory": [r.model_dump(mode="json") for r in memory.review_history if r.task_id == task_id],
        "implementationRef": impl_ref,
        "testResult": test_result,
    }


# ---------------------------------------------------------------------------
# FEAT-019 — task-list review + docs-update checkpoints
# ---------------------------------------------------------------------------


def apply_task_review_verdict(
    payload: Mapping[str, Any],
    current_memory: Mapping[str, Any],
) -> dict[str, Any]:
    """Verdict for the independent task-list review checkpoint.

    On approve: empty patch (predicate reads ``verdict`` from the surfaced
    payload fields; no memory write needed).  On reject: persist the
    reviewer's feedback to ``rejections["confirm_task_review"]`` so the
    next ``confirm_tasks`` intake can surface it.
    """
    parsed = TasksReviewedPayload.model_validate(payload)
    if parsed.verdict == "reject":
        return _rejection_patch("confirm_task_review", parsed.feedback, current_memory)
    return {}


def apply_docs_update_verdict(
    payload: Mapping[str, Any],
    current_memory: Mapping[str, Any],
) -> dict[str, Any]:
    """Verdict for the docs-update / definition-of-done checkpoint.

    On approve: empty patch.  On reject: persist feedback to
    ``rejections["confirm_docs_update"]`` for the next loop iteration.
    """
    parsed = DocsUpdateConfirmedPayload.model_validate(payload)
    if parsed.verdict == "reject":
        return _rejection_patch("confirm_docs_update", parsed.feedback, current_memory)
    return {}


def intake_for_confirm_task_review(current_memory: Mapping[str, Any]) -> dict[str, Any]:
    """Expose the authored task list and validator result to the reviewer (FEAT-019/020)."""
    memory = read_lifecycle_memory(current_memory)
    prior_feedback: str | None = None
    rejections_raw: Any = current_memory.get("rejections") or {}
    if isinstance(rejections_raw, dict):
        prior_entry = cast("dict[str, Any]", rejections_raw).get("confirm_task_review")
        if isinstance(prior_entry, dict):
            prior_feedback = str(cast("dict[str, Any]", prior_entry).get("feedback") or "")
    validator_result: Any = None
    validator_results_raw: Any = current_memory.get("validatorResults") or {}
    if isinstance(validator_results_raw, dict):
        validator_result = cast("dict[str, Any]", validator_results_raw).get("tasks")
    return {
        "tasks": [t.model_dump(mode="json", by_alias=True) for t in memory.tasks],
        "priorFeedback": prior_feedback,
        "validatorResult": validator_result,
    }


def apply_tests_completed(
    payload: Mapping[str, Any],
    current_memory: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist the operator-reported test result for the current task.

    Writes to the top-level ``testResults[task_id]`` sidecar so
    ``intake_for_human_review`` can surface it as ``testResult`` in
    ``human_review_implementation`` nodeInputs.
    """
    parsed = TestsCompletedPayload.model_validate(payload)
    memory = read_lifecycle_memory(current_memory)
    task_id = memory.current_task_id
    if not task_id:
        return {}
    existing_raw: Any = current_memory.get("testResults") or {}
    existing: dict[str, Any] = (
        {str(k): v for k, v in cast("dict[str, Any]", existing_raw).items()}
        if isinstance(existing_raw, dict)
        else {}
    )
    existing[task_id] = {
        "passed": parsed.passed,
        "output": parsed.output,
    }
    return {"testResults": existing}


def intake_for_run_tests(current_memory: Mapping[str, Any]) -> dict[str, Any]:
    """Surface current task + implementation ref so the operator knows what to test."""
    memory = read_lifecycle_memory(current_memory)
    task_id = memory.current_task_id or ""
    current_task = next(
        (t for t in memory.tasks if t.id == task_id), None
    )
    impl_refs_raw: Any = current_memory.get("implementation_refs") or {}
    impl_ref: dict[str, Any] | None = None
    if isinstance(impl_refs_raw, dict) and task_id:
        impl_ref = cast("dict[str, Any]", impl_refs_raw).get(task_id)
    return {
        "currentTask": current_task.model_dump(mode="json") if current_task else None,
        "implementationRef": impl_ref,
        "instructions": (
            "Run the project's test suite and report the result. "
            "Signal with {passed: true/false, output: '<pytest summary>'}."
        ),
    }


def intake_for_confirm_docs_update(current_memory: Mapping[str, Any]) -> dict[str, Any]:
    """Expose the work item and completed task summary for the docs-update gate (FEAT-019)."""
    memory = read_lifecycle_memory(current_memory)
    prior_feedback: str | None = None
    rejections_raw: Any = current_memory.get("rejections") or {}
    if isinstance(rejections_raw, dict):
        prior_entry = cast("dict[str, Any]", rejections_raw).get("confirm_docs_update")
        if isinstance(prior_entry, dict):
            prior_feedback = str(cast("dict[str, Any]", prior_entry).get("feedback") or "")
    return {
        "workItem": memory.work_item.model_dump(mode="json", by_alias=True) if memory.work_item else None,
        "tasks": [t.model_dump(mode="json", by_alias=True) for t in memory.tasks],
        "completedTaskIds": list(memory.completed_task_ids),
        "priorFeedback": prior_feedback,
    }


__all__ = [
    "AssignmentConfirmedPayload",
    "BriefConfirmedPayload",
    "DocsUpdateConfirmedPayload",
    "ImplementationCompletePayload",
    "MockupApprovedPayload",
    "PlanConfirmedPayload",
    "ReviewCompletedPayload",
    "TasksConfirmedPayload",
    "TasksReviewedPayload",
    "TestsCompletedPayload",
    "apply_assignment_confirmation",
    "apply_brief_correction",
    "apply_docs_update_verdict",
    "apply_implementation_signal",
    "apply_mockup_approval",
    "apply_plan_correction",
    "apply_review_verdict",
    "apply_task_review_verdict",
    "apply_tasks_correction",
    "apply_tests_completed",
    "intake_for_confirm_assignment",
    "intake_for_confirm_brief",
    "intake_for_confirm_docs_update",
    "intake_for_confirm_mockup",
    "intake_for_confirm_plan",
    "intake_for_confirm_task_review",
    "intake_for_confirm_tasks",
    "intake_for_human_review",
    "intake_for_request_implementation",
    "intake_for_run_tests",
]
