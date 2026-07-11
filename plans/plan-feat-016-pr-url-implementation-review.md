# Implementation Plan: FEAT-016 — PR URL in Implementation Review Context

## Task Reference
- **Tasks:** T-314, T-315, T-316, T-317, T-318
- **Feature:** FEAT-016
- **Complexity:** All S
- **Workflow:** standard

## Overview

Adds optional `prUrl`, `commitSha`, `summary` fields to the `implementation-complete` signal. The orchestrator persists them in `RunMemory.data["implementation_refs"][task_id]` and surfaces them in the `human_review_implementation` node's `nodeInputs.implementationRef` so the human reviewer can open the PR directly from DevHub.

---

## Implementation Steps

### Step 1: Add `ImplementationCompletePayload` schema
**File:** `src/app/modules/ai/executors/lifecycle_manual_patches.py`
**Action:** Modify

Add after `ReviewCompletedPayload` (line 132). All fields optional, `extra="forbid"` via `_PayloadConfig`. Camel aliases auto-generated (`pr_url` → `prUrl`, `commit_sha` → `commitSha`).

### Step 2: Add `apply_implementation_signal` patch builder
**File:** `src/app/modules/ai/executors/lifecycle_manual_patches.py`
**Action:** Modify

Add after `apply_review_verdict`. Pattern mirrors `apply_plan_correction` / `apply_assignment_confirmation`:
- `read_lifecycle_memory(current_memory).current_task_id` for the key
- Merge into existing `implementation_refs` dict (don't overwrite other tasks)
- Return `{}` when all fields absent or `current_task_id` is None

### Step 3: Extend `intake_for_human_review` with `implementationRef`
**File:** `src/app/modules/ai/executors/lifecycle_manual_patches.py`
**Action:** Modify

Read `current_memory.get("implementation_refs", {}).get(task_id)` and add as `"implementationRef"` key. Value is `None` when absent (explicit, so DevHub can render "no PR submitted").

### Step 4: Export new symbols in `__all__`
**File:** `src/app/modules/ai/executors/lifecycle_manual_patches.py`
**Action:** Modify

Add `"ImplementationCompletePayload"` and `"apply_implementation_signal"` to `__all__`.

### Step 5: Wire patch builder into `request_implementation` binding
**File:** `src/app/modules/ai/executors/bootstrap.py`
**Action:** Modify

In the `register_lifecycle_v03` section (line ~865), extend the local import block for `intake_for_request_implementation` to also import `apply_implementation_signal`, then add `memory_patch_builder=apply_implementation_signal` to the `HumanExecutor(...)` call. The binding lives in `register_lifecycle_v03` (shared base), which is correct — `@0.4.0-manual` inherits it automatically, and `@0.3.0` gets backward-compat no-op behaviour for empty payloads.

### Step 6: Update `docs/api-spec.md`
**File:** `docs/api-spec.md`
**Action:** Modify

Extend the `implementation-complete` signal docs with optional payload fields and add a changelog entry.

### Step 7: Update `docs/data-model.md`
**File:** `docs/data-model.md`
**Action:** Modify

Add `implementation_refs` sidecar to the RunMemory section and add a changelog entry.

### Step 8: Unit tests
**File:** `tests/modules/ai/executors/test_lifecycle_manual_patches.py`
**Action:** Modify

Add test cases for `apply_implementation_signal` (with prUrl, empty, multi-task, no task_id in memory) and for the extended `intake_for_human_review`.

---

## Files Affected

| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/executors/lifecycle_manual_patches.py` | Modify | Add schema, builder, extend intake, export |
| `src/app/modules/ai/executors/bootstrap.py` | Modify | Import + wire `apply_implementation_signal` |
| `docs/api-spec.md` | Modify | Document payload fields + changelog |
| `docs/data-model.md` | Modify | Document `implementation_refs` sidecar + changelog |
| `tests/modules/ai/executors/test_lifecycle_manual_patches.py` | Modify | Unit tests |

---

## Edge Cases & Risks

- **Empty payload** (`{}`) — all fields None → builder returns `{}` → no memory write → backward compat
- **`current_task_id` None** — shouldn't happen in practice (signal fires mid-task), but builder returns `{}` defensively
- **Multi-task run** — builder merges into existing `implementation_refs` dict; second task write does not overwrite first
- **`implementationRef: None`** in intake — explicit null preferred over missing key so DevHub can distinguish "no PR yet" from "field not supported"
