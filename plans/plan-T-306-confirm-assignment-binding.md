# Implementation Plan: T-306 — Wire `confirm_assignment` HumanExecutor binding into `register_lifecycle_v04_manual`

## Task Reference
- **Task ID:** T-306
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** Symmetric extension — one new block of the same shape as the four existing checkpoints. Placement next to `confirm_plan` keeps the four pre-engine-commit checkpoints visually grouped.

## Overview
Add a fifth `registry.register(...)` call in `register_lifecycle_v04_manual` for the `confirm_assignment` node, using `HumanExecutor` with `expected_signal_name="assignment-confirmed"` and the builder from T-305. Update the function's import block, docstring, and log line. No other change.

## Implementation Steps

### Step 1: Extend the local imports
**File:** `src/app/modules/ai/executors/bootstrap.py`
**Action:** Modify

Locate the local import block inside `register_lifecycle_v04_manual` (lines ~1143-1149):

```python
from app.modules.ai.executors.lifecycle_manual_patches import (
    apply_brief_correction,
    apply_plan_correction,
    apply_review_verdict,
    apply_tasks_correction,
)
```

Add `apply_assignment_confirmation` to the import list (keep alphabetical):

```python
from app.modules.ai.executors.lifecycle_manual_patches import (
    apply_assignment_confirmation,
    apply_brief_correction,
    apply_plan_correction,
    apply_review_verdict,
    apply_tasks_correction,
)
```

### Step 2: Add the new binding
**File:** `src/app/modules/ai/executors/bootstrap.py`
**Action:** Modify

Insert a new `registry.register(...)` block between the existing `confirm_plan` block (lines ~1183-1191) and the human-reviewer block (line ~1193). Match the shape of the four existing blocks byte-for-byte:

```python
registry.register(
    agent_ref,
    "confirm_assignment",
    HumanExecutor(
        ref="human:confirm_assignment",
        expected_signal_name="assignment-confirmed",
        memory_patch_builder=apply_assignment_confirmation,
    ),
)
```

Keep the comment headers intact ("# 2. Four new human checkpoints." stays, but the count is now five — update to "# 2. Five human checkpoints (gates at brief / tasks / assignment / plan)."). The "# 3. Human reviewer ..." comment is unchanged.

### Step 3: Update the docstring
**File:** `src/app/modules/ai/executors/bootstrap.py`
**Action:** Modify

In the docstring at line ~1131-1142, change:

> "then registers the four new ``HumanExecutor`` checkpoint bindings plus the human reviewer..."

to:

> "then registers the five ``HumanExecutor`` checkpoint bindings (brief, tasks, assignment, plan, review) plus the human reviewer..."

(The wording above lists six items; reword as: "the four pre-engine-commit ``HumanExecutor`` checkpoint bindings (brief, tasks, assignment, plan) plus the human reviewer in place of the LLM ``review_implementation``.")

### Step 4: Update the log line
**File:** `src/app/modules/ai/executors/bootstrap.py`
**Action:** Modify

In the `logger.info(...)` call at line ~1204-1208, change `4 human checkpoints` to `5 human checkpoints`. Update the parenthetical breakdown accordingly:

```python
logger.info(
    "register_lifecycle_v04_manual: agent_ref=%s registered (5 human "
    "checkpoints + human reviewer + v0.3.0 shared bindings)",
    agent_ref,
)
```

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/executors/bootstrap.py` | Modify | Local import + one `registry.register` block + docstring/log line text updates. |

## Edge Cases & Risks
- **Import-order:** the local import sits inside the function body (lazy import pattern used by the existing block), so any cyclic-import risk introduced by extending `lifecycle_manual_patches.py` in T-305 is contained.
- **Coverage validator:** `validate_executor_coverage` runs at lifespan startup and asserts every node in the YAML has a binding (or a `no_executor` exemption). Because T-307 adds the YAML node *after* T-306 lands, intermediate boots between T-306 and T-307 will still pass — the binding is registered but the node doesn't exist yet, which is allowed (the validator checks the reverse direction). If a developer accidentally lands T-307 before T-306, boot will fail loudly. Land in T-305 → T-306 → T-307 order.
- **`register_lifecycle_v03` left alone:** explicit. The shared helper must remain unaware of assignment.
- **No refactor temptation:** the four-then-five blocks must stay explicit. A loop would obscure the symmetry future readers (or a sixth checkpoint) need.

## Acceptance Verification
- [ ] `register_lifecycle_v04_manual` registers an executor for `(agent_ref, "confirm_assignment")` — verified by inspecting the registry in a unit test or via the integration test in T-309.
- [ ] Executor type is `HumanExecutor` with `expected_signal_name="assignment-confirmed"` and `memory_patch_builder=apply_assignment_confirmation`.
- [ ] Docstring lists five checkpoints.
- [ ] Log line shows `5 human checkpoints`.
- [ ] `from app.modules.ai.executors.lifecycle_manual_patches import apply_assignment_confirmation` succeeds (T-305 export sanity check).
- [ ] Existing four checkpoints' blocks are byte-unchanged (diff inspection).
- [ ] `uv run uvicorn app.main:app` boots cleanly (after T-307 lands too) — runs the executor coverage validator.
