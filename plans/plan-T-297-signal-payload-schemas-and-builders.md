# Implementation Plan: T-297 — Pydantic signal payload schemas + four memory-patch builders

## Task Reference
- **Task ID:** T-297
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** FEAT-015 §4.1 — the four checkpoint signals (`brief-confirmed`, `tasks-confirmed`, `plan-confirmed`, `review-completed`) each carry an optional or required payload that translates to a `RunMemory.data` patch. Each builder is a pure function with its own Pydantic schema for input validation.

## Overview
Create `src/app/modules/ai/executors/lifecycle_manual_patches.py` with four Pydantic v2 payload models and four pure builder functions. Each builder validates its input, optionally reads from `current_memory`, and returns a patch dict targeting the `lifecycle.v1` namespace. The shape of the review-history entry MUST match `_patch_review` (the LLM reviewer's builder in `bootstrap.py`) so downstream `review_passed` and `correct_implementation` nodes read the same memory contract.

## Implementation Steps

### Step 1: Create the new module
**File:** `src/app/modules/ai/executors/lifecycle_manual_patches.py`
**Action:** Create

Skeleton:

```python
"""Memory-patch builders for lifecycle-agent@0.4.0-manual (FEAT-015 / T-297).

Each function consumes a (signal_payload, current_memory) pair and returns
a patch dict the runtime merges into RunMemory.data via the
``__memory_patch`` hook on the dispatch envelope's result.

These are pure functions. No I/O. No session. The HumanExecutor (T-296)
passes ``current_memory`` in from the DB read it already performs.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from app.modules.ai.enums import WorkItemType
from app.modules.ai.tools.lifecycle.memory import (
    LIFECYCLE_MEMORY_NS,
    read_lifecycle_memory,
)
```

### Step 2: `BriefConfirmedPayload` + `_apply_brief_correction`
**File:** `src/app/modules/ai/executors/lifecycle_manual_patches.py`
**Action:** Modify

```python
class _WorkItemCorrection(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)
    title: str | None = None
    type: WorkItemType | None = None


class BriefConfirmedPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)
    work_item: _WorkItemCorrection | None = None


def _apply_brief_correction(
    payload: Mapping[str, Any],
    current_memory: Mapping[str, Any],
) -> dict[str, Any]:
    parsed = BriefConfirmedPayload.model_validate(payload)
    if parsed.work_item is None:
        return {}
    mem = read_lifecycle_memory(current_memory)
    if mem.work_item is None:
        # No work_item in memory yet — load_work_item hasn't run.  The
        # operator delivered the signal too early; refuse to fabricate
        # one.
        raise ValueError(
            "brief-confirmed received but lifecycle.v1.work_item is not yet "
            "populated — wait for load_work_item to complete."
        )
    merged = mem.work_item.model_dump()
    if parsed.work_item.title is not None:
        merged["title"] = parsed.work_item.title
    if parsed.work_item.type is not None:
        merged["type"] = parsed.work_item.type.value
    return {LIFECYCLE_MEMORY_NS: {"work_item": merged}}
```

### Step 3: `TasksConfirmedPayload` + `_apply_tasks_correction`
**File:** `src/app/modules/ai/executors/lifecycle_manual_patches.py`
**Action:** Modify

```python
class _TaskItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)
    id: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1)
    summary: str | None = None


class TasksConfirmedPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)
    tasks: list[_TaskItem] | None = None

    @field_validator("tasks")
    @classmethod
    def _non_empty_if_present(cls, v: list[_TaskItem] | None) -> list[_TaskItem] | None:
        if v is not None and len(v) == 0:
            raise ValueError("tasks list must be non-empty when provided")
        return v


def _apply_tasks_correction(
    payload: Mapping[str, Any],
    current_memory: Mapping[str, Any],
) -> dict[str, Any]:
    parsed = TasksConfirmedPayload.model_validate(payload)
    if parsed.tasks is None:
        return {}
    return {
        LIFECYCLE_MEMORY_NS: {
            "tasks": [t.model_dump(by_alias=False) for t in parsed.tasks]
        }
    }
```

### Step 4: `PlanConfirmedPayload` + `_apply_plan_correction`
**File:** `src/app/modules/ai/executors/lifecycle_manual_patches.py`
**Action:** Modify

```python
class PlanConfirmedPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)
    plan: str | None = None


def _apply_plan_correction(
    payload: Mapping[str, Any],
    current_memory: Mapping[str, Any],
) -> dict[str, Any]:
    parsed = PlanConfirmedPayload.model_validate(payload)
    if parsed.plan is None:
        return {}
    mem = read_lifecycle_memory(current_memory)
    if mem.current_task_id is None:
        raise ValueError(
            "plan-confirmed received with no current_task_id in lifecycle memory — "
            "wait for assign_task / generate_plan to set it."
        )
    current_plans = dict(mem.task_plans or {})
    current_plans[mem.current_task_id] = parsed.plan
    return {LIFECYCLE_MEMORY_NS: {"taskPlans": current_plans}}
```

### Step 5: `ReviewCompletedPayload` + `_apply_review_verdict`
**File:** `src/app/modules/ai/executors/lifecycle_manual_patches.py`
**Action:** Modify

This is the load-bearing one — shape MUST match `_patch_review` (the LLM reviewer's builder) modulo the `reviewer` field.

```python
class ReviewCompletedPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)
    verdict: Literal["pass", "fail"]
    feedback: str | None = None


def _apply_review_verdict(
    payload: Mapping[str, Any],
    current_memory: Mapping[str, Any],
) -> dict[str, Any]:
    parsed = ReviewCompletedPayload.model_validate(payload)
    mem = read_lifecycle_memory(current_memory)
    if mem.current_task_id is None:
        raise ValueError(
            "review-completed received with no current_task_id in lifecycle memory."
        )
    task_id = mem.current_task_id
    attempt = sum(1 for r in mem.review_history if r.task_id == task_id) + 1
    new_entry: dict[str, Any] = {
        "taskId": task_id,
        "verdict": parsed.verdict,
        "feedback": parsed.feedback,
        "attempt": attempt,
        "reviewedAt": datetime.now(UTC).isoformat(),
        "reviewer": "human",
    }
    # Append to existing reviewHistory[]; do not replace.
    history = [r.model_dump(by_alias=True) for r in mem.review_history]
    history.append(new_entry)
    return {LIFECYCLE_MEMORY_NS: {"reviewHistory": history}}
```

Cross-reference: open `executors/bootstrap.py::_patch_review` and verify the field names + `attempt` increment match. If the LLM path emits camelCase JSON keys, this builder must do the same. If it emits snake_case, switch all keys (`taskId` → `task_id`, etc.). The pattern in v0.3.0 favors camelCase JSON aliases — confirm before merging.

### Step 6: Export the public API
**File:** `src/app/modules/ai/executors/lifecycle_manual_patches.py`
**Action:** Modify (append)

```python
__all__ = [
    "BriefConfirmedPayload",
    "TasksConfirmedPayload",
    "PlanConfirmedPayload",
    "ReviewCompletedPayload",
    "_apply_brief_correction",
    "_apply_tasks_correction",
    "_apply_plan_correction",
    "_apply_review_verdict",
]
```

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/executors/lifecycle_manual_patches.py` | Create | Four payload schemas + four builder functions; ~150 lines. |

## Edge Cases & Risks
- **Review-history shape divergence.** `_patch_review` in bootstrap.py is the LLM reviewer's writer. If its shape changes in a future refactor, this human reviewer drifts silently. Mitigation: add a comment at the top of `_apply_review_verdict` pointing at `_patch_review` as the parity-anchor, and let T-302's integration test catch divergence by exercising both paths in adjacent tests (or by importing `_patch_review` and re-using its body if it's a pure function).
- **Empty payload semantics.** All four builders treat "field absent or null" as "no edit, approve as-is" — empty patch `{}` returned. This is by design (FEAT-015 §9 — "Empty signal payload at `confirm_*` means 'approve without edits.'") and must NOT raise.
- **`tasks` of length 0 vs `tasks` absent.** Absent → `{}` (approve unchanged). Length 0 → `ValueError` from the Pydantic validator (deliberate rejection per FEAT-015 §9). Two distinct shapes, two distinct outcomes.
- **CamelCase JSON aliases.** All payload schemas use `populate_by_name=True` + `alias_generator=to_camel`. Callers can use snake_case (`work_item`) or camelCase (`workItem`); validation accepts both. Patches written into memory use the project's convention — match the existing `lifecycle.v1` field naming exactly.
- **Type coercion on `WorkItemType`.** `_WorkItemCorrection.type` accepts the enum or its string value. Pydantic v2's behavior with `StrEnum` already permits both; verify with a unit test that passing `"FEAT"` works.

## Acceptance Verification
- [ ] AC-1 — Schemas validate the happy-path payloads documented in T-300; reject the malformed payloads documented in T-300.
- [ ] AC-2 — Each builder returns the exact shape described in this plan; covered by T-300's unit tests (Step 1's overview is implementation guidance, not an AC — AC verification lives in T-300).
- [ ] AC-3 — `_apply_review_verdict`'s output dict is byte-compatible with `_patch_review`'s shape modulo the `reviewer` discriminator (manual diff; pinned in T-302's integration test).
- [ ] AC-4 — `pyright` clean on the new module.
