# Implementation Plan: T-305 — Add `AssignmentConfirmedPayload` schema + `apply_assignment_confirmation` builder

## Task Reference
- **Task ID:** T-305
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** The four current manual-variant checkpoints diverge only in payload shape and target memory key. The plan-sidecar precedent (`apply_plan_correction`) documents that engine-aux sidecars sit beside `lifecycle.v1`, not nested — `assignments` follows the same shape, keyed by engine task id. Sidecar choice keeps `LifecycleTask` byte-identical to v0.3.0 so the "variants are peers" pattern holds.

## Overview
Add the fifth payload schema + memory-patch builder to `lifecycle_manual_patches.py`, symmetric with the four existing ones (brief / tasks / plan / review). The builder writes a top-level `assignments` sidecar keyed by engine task id. No change to `LifecycleMemory` Pydantic model — the sidecar lives in `RunMemory.data` JSON alongside `plans`.

## Implementation Steps

### Step 1: Add the Pydantic payload schema
**File:** `src/app/modules/ai/executors/lifecycle_manual_patches.py`
**Action:** Modify

Append a new schema class `AssignmentConfirmedPayload` at the end of the existing schemas block (after `ReviewCompletedPayload`):

```python
class AssignmentConfirmedPayload(BaseModel):
    """Payload for the ``assignment-confirmed`` signal (IMP-004)."""

    model_config = _PayloadConfig

    assignee: str = Field(..., min_length=1)
    task_id: str | None = None

    @field_validator("assignee")
    @classmethod
    def _assignee_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("assignee must not be blank")
        return value
```

Reuse the module-level `_PayloadConfig` (line ~54) — do not redefine. The `extra="forbid"` in that config rejects typos in the payload at validation time, matching the four sibling schemas.

### Step 2: Add the builder function
**File:** `src/app/modules/ai/executors/lifecycle_manual_patches.py`
**Action:** Modify

Append the builder after `apply_review_verdict` (the last existing builder). Mirror the parameter shape of `apply_plan_correction` — it's the closest analog because it also reads `current_task_id` from memory:

```python
def apply_assignment_confirmation(
    payload: Mapping[str, Any],
    *,
    lifecycle_memory: Mapping[str, Any],
) -> dict[str, Any]:
    """Builder for the ``assignment-confirmed`` signal (IMP-004 / T-305).

    Resolves the target task id (payload override or current_task_id from
    memory) and merges the new assignee into the ``assignments`` top-level
    sidecar.  Mirrors the merge-preserve pattern from ``_patch_generate_plan``
    so loop-back over a multi-task work item preserves prior assignees.
    """
    validated = AssignmentConfirmedPayload.model_validate(payload)

    lifecycle = read_lifecycle_memory(lifecycle_memory)
    target_task_id = validated.task_id or lifecycle.current_task_id
    if not target_task_id:
        raise ValueError(
            "assignment-confirmed received with no resolvable task id "
            "(payload.taskId absent and current_task_id is None)"
        )

    existing = lifecycle_memory.get("assignments") or {}
    if not isinstance(existing, Mapping):
        raise ValueError(
            "memory['assignments'] is present but not a mapping; refusing to overwrite"
        )

    merged = dict(existing)
    merged[target_task_id] = validated.assignee
    return {"assignments": merged}
```

Note: the function reads the raw `lifecycle_memory` dict for the `assignments` sidecar (not via `read_lifecycle_memory` — that helper returns the typed `lifecycle.v1` slice only). This matches `_patch_generate_plan`'s pattern in `bootstrap.py`, which reads `current_memory.get("plans")` directly.

### Step 3: Export from module `__all__`
**File:** `src/app/modules/ai/executors/lifecycle_manual_patches.py`
**Action:** Modify

If the module has an `__all__` block, add `AssignmentConfirmedPayload` and `apply_assignment_confirmation`. If not (current module appears to rely on direct imports), no change — but verify the import in T-306's bootstrap update works.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/executors/lifecycle_manual_patches.py` | Modify | Append schema + builder; optionally extend `__all__`. |

## Edge Cases & Risks
- **Empty/whitespace `assignee`:** schema-level rejection via `min_length=1` + `_assignee_not_blank` validator. The HumanExecutor's failure path (T-296) converts `ValidationError` into a failed dispatch → stop_reason=error.
- **`payload.task_id` provided but `current_task_id` differs:** payload wins (allows operator to override mid-flow if reassignment surface is ever added). Document in the docstring.
- **`current_task_id` is None and no `task_id` in payload:** `ValueError` — caught by HumanExecutor, fails the dispatch cleanly.
- **`assignments` already present as non-dict:** defensive check; raises `ValueError` rather than silently overwriting.
- **Mutation safety:** `merged = dict(existing)` ensures the function never mutates the input memory dict. Critical because the runtime passes `RunMemory.data` directly.
- **Sidecar vs. nested decision:** locked. Sidecar matches `plans`. Nesting under `lifecycle.v1.tasks[*].assignee` would require modifying `LifecycleTask` (Pydantic model in `tools/lifecycle/memory.py`), which violates the "v0.3.0 memory byte-unchanged" constraint.

## Acceptance Verification
- [ ] `AssignmentConfirmedPayload(assignee="alice")` validates → unit test in T-308.
- [ ] `AssignmentConfirmedPayload(assignee="")` and `assignee="  "` raise `ValidationError` → unit test in T-308.
- [ ] `AssignmentConfirmedPayload(assignee="alice", taskId="t-1").task_id == "t-1"` (alias roundtrip) → unit test in T-308.
- [ ] Builder happy path with explicit `task_id` returns `{"assignments": {"t-1": "alice"}}` → unit test in T-308.
- [ ] Builder fallback to `lifecycle_memory.current_task_id` works → unit test in T-308.
- [ ] Builder raises `ValueError` when neither source resolves a task id → unit test in T-308.
- [ ] Builder preserves prior `assignments` entries on merge → unit test in T-308 (assertion 4 in T-309 also catches this).
- [ ] Builder never mutates input — assert `lifecycle_memory` unchanged after call → unit test in T-308.
- [ ] `pyright` clean.
