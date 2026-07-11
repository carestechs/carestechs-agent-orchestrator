# FEAT-017 — Mockup Task Conditional Flow

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-017 |
| **Name** | Conditional mockup generation and approval step for mockup-kind tasks |
| **Target Version** | `lifecycle-agent@0.5.0-manual` |
| **Status** | Open |
| **Priority** | High |
| **Requested By** | Carlos |
| **Date Created** | 2026-07-11 |

---

## 2. User Story

**As an** operator running a `lifecycle-agent@0.5.0-manual` work item, **I want** tasks with `kind: "mockup"` to automatically route through a generate-and-approve mockup step before the implementation plan is authored, **so that** UI work is validated visually by a human before any code is written.

---

## 3. Goal

When a task in `LifecycleMemory.tasks` carries `kind: "mockup"`, the flow inserts two extra nodes after `assign_task`:

1. `generate_mockup` — LLM-content executor authors an HTML mockup for the task.
2. `confirm_mockup` — HumanExecutor pauses for operator approval via a `mockup-approved` signal.

On approval, the flow converges back to the existing `generate_plan` node and proceeds unchanged. Tasks of any other kind (`feature`, `bug`, `chore`) bypass both nodes entirely.

The rest of the flow — assignment, planning, implementation, review — is unchanged. This FEAT only extends the per-task branch point between `assign_task` and `generate_plan`.

---

## 4. Architectural Decisions

### 4.1 Branch point: after `assign_task`, not after `confirm_tasks`

The branch on task kind belongs after `assign_task` (not at `propose_tasks` or `confirm_tasks`) because the per-task loop re-enters at `confirm_assignment → assign_task` for each task. Branching here ensures the mockup path fires correctly for every mockup task in a multi-task work item without any special looping logic.

### 4.2 New agent version — `@0.5.0-manual`

The YAML gains two new nodes and one new transition branch. Following the existing pattern (each variant ships its own YAML + bootstrap function), this creates `lifecycle-agent@0.5.0-manual.yaml` and `register_lifecycle_v05_manual(...)` in `bootstrap.py`. The v0.4.0-manual bootstrap is unchanged; `register_all_executors` routes by `agent_ref.startswith("lifecycle-agent@0.5.0-manual")` to the new helper, which delegates to `register_lifecycle_v04_manual` (reusing all v0.4.0 bindings) then layers on the two new executor bindings.

### 4.3 `kind` field on `LifecycleTask` and `GenerateTasksTask`

`kind: Literal["feature", "mockup", "bug", "chore"]` defaults to `"feature"`. The field is added to both `LifecycleTask` (in `tools/lifecycle/memory.py`) and `GenerateTasksTask` (in `executors/lifecycle_schemas.py`). Old runs that pre-date this field validate unchanged via the existing `default="feature"`. The `_patch_generate_tasks` builder in `bootstrap.py` passes `kind` through.

### 4.4 New predicate — `current_task_is_mockup`

Registered in `flow_predicates.py`. Pure function over `(memory, last)` — reads `current_task_id` from `read_lifecycle_memory(memory)`, finds that task in `lifecycle.v1.tasks`, and returns `task.kind == "mockup"`. Returns `False` if `current_task_id` is `None` or the task is not found (safe default: skip the mockup path).

### 4.5 Mockup memory sidecar — top-level `mockups`

Generated mockup HTML is stored in a top-level `mockups[taskId]` sidecar (same pattern as `plans`, `assignments`, `implementation_refs`). Shape:

```json
{
  "mockups": {
    "T-001": {
      "mockup_html": "<!DOCTYPE html>...",
      "description": "Login screen — email + password + forgot link"
    }
  }
}
```

The `generate_mockup` memory patch builder writes to this sidecar. The `confirm_mockup` HumanExecutor's `intake_builder` surfaces `mockupHtml` and `mockupDescription` in `nodeInputs` so DevHub can render the mockup inline for the operator to review.

### 4.6 `confirm_mockup` reuses existing checkpoint signal pattern

The `mockup-approved` signal follows the same approve/reject contract as all other manual checkpoints: `{ "outcome": "approve" | "reject", "feedback"?: string }`. `checkpoint_approved` and `checkpoint_rejections_under_bound` predicates apply unchanged — no new predicates are needed for the confirmation branch.

### 4.7 Rejection loop for `generate_mockup`

If the operator rejects the mockup, the flow loops back to `generate_mockup` carrying the rejection feedback (same `_rejection_patch` + `rejectionFeedback` binding pattern as `load_work_item` and `generate_plan`). `checkpoint_rejections_under_bound` gates the loop; exhausting the budget routes to `terminate_rejection_budget`.

---

## 5. Scope

**In scope:**
- `kind` field on `LifecycleTask` + `GenerateTasksTask`
- `current_task_is_mockup` predicate
- `generate_mockup` LLM-content executor + system prompt + `GenerateMockupResult` schema + memory patch
- `confirm_mockup` HumanExecutor + intake builder (surfaces mockup HTML to DevHub)
- `lifecycle-agent@0.5.0-manual.yaml` — two new nodes + updated `assign_task` transition
- `register_lifecycle_v05_manual` bootstrap function
- `mockup-approved` signal documented in `api-spec.md`
- `docs/data-model.md` — `mockups` sidecar entry

**Out of scope:**
- Mockup rendering in DevHub (DevHub work item)
- Mockup storage beyond `RunMemory.data` (no separate DB table in v1)
- Propagating `kind` to `@0.3.0` or `@0.4.0-manual` (non-breaking; those versions simply don't have the branch)
- Parallel task execution (future work)

---

## 6. Acceptance Criteria

- **AC-1:** A task with `kind: "mockup"` in a `@0.5.0-manual` run routes through `generate_mockup → confirm_mockup` before `generate_plan`. A task with any other kind skips both nodes.
- **AC-2:** `generate_mockup` produces a `mockups[taskId]` sidecar entry containing `mockup_html` and `description`.
- **AC-3:** `confirm_mockup` node inputs surfaced via `intake_builder` include `mockupHtml` and `mockupDescription`.
- **AC-4:** Rejecting `confirm_mockup` loops back to `generate_mockup` with feedback injected; exhausting the rejection budget routes to `terminate_rejection_budget`.
- **AC-5:** Tasks with `kind: "feature"` (default) in a `@0.5.0-manual` run are unaffected — they proceed directly from `assign_task` to `generate_plan`.
- **AC-6:** `@0.4.0-manual` and `@0.3.0` runs are unaffected by all schema changes (backward-compat `default="feature"` on `kind`).
- **AC-7:** `validate_executor_coverage` passes at lifespan startup for `@0.5.0-manual`.

---

## 7. Files to Modify / Create

| File | Change |
|------|--------|
| `src/app/modules/ai/tools/lifecycle/memory.py` | Add `kind` field to `LifecycleTask` |
| `src/app/modules/ai/executors/lifecycle_schemas.py` | Add `kind` to `GenerateTasksTask`; add `GenerateMockupResult` |
| `src/app/modules/ai/executors/bootstrap.py` | Pass `kind` in `_patch_generate_tasks`; add `generate_mockup` + `confirm_mockup` bindings; add `register_lifecycle_v05_manual` |
| `src/app/modules/ai/flow_predicates.py` | Add `current_task_is_mockup` predicate |
| `src/app/modules/ai/executors/prompts/lifecycle/generate_mockup.md` | New system prompt |
| `src/app/modules/ai/executors/prompts/lifecycle/generate_tasks.md` | Document `kind` field + mockup guidance |
| `agents/lifecycle-agent@0.5.0-manual.yaml` | New agent YAML with `generate_mockup` + `confirm_mockup` nodes |
| `docs/api-spec.md` | Add `mockup-approved` signal contract + changelog |
| `docs/data-model.md` | Add `mockups` top-level sidecar + changelog |

---

## 8. Tasks

> To be generated via `.ai-framework/prompts/feature-tasks.md`.
