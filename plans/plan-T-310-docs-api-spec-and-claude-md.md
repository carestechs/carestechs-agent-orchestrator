# Implementation Plan: T-310 — Document `assignment-confirmed` signal + variant memory note

## Task Reference
- **Task ID:** T-310
- **Type:** Docs
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** Doc-first discipline — IMP-004 §11 lists `docs/api-spec.md` as the contract surface. Future operators (and future agents) need the signal documented in the same place they'd look for `plan-confirmed`.

## Overview
Add the `assignment-confirmed` signal row to the manual-variant signal contract table in `docs/api-spec.md`, append a changelog entry, and update the "Lifecycle agent variants are peers" paragraph in `CLAUDE.md` to mention the variant-specific `assignments` sidecar.

## Implementation Steps

### Step 1: Locate the signal contract table
**File:** `docs/api-spec.md`
**Action:** Read

Search for `brief-confirmed`, `tasks-confirmed`, `plan-confirmed`, `review-completed` to find the existing table (added by FEAT-015 / T-303). Inspect column layout — likely: Signal name | Variant | Required payload fields | Optional payload fields | Idempotency key | Notes.

### Step 2: Add the `assignment-confirmed` row
**File:** `docs/api-spec.md`
**Action:** Modify

Insert between `tasks-confirmed` and `plan-confirmed` rows (matching flow order). Column values (adjust to match actual column names in the existing table):

| Signal | Variant | Required | Optional | Idempotency | Notes |
|--------|---------|----------|----------|-------------|-------|
| `assignment-confirmed` | `lifecycle-agent@0.4.0-manual` only | `assignee: string (non-empty)` | `taskId: string` (defaults to `current_task_id`) | `(run_id, "assignment-confirmed", task_id)` — duplicates return 202 with `meta.alreadyReceived=true` | Pauses before T5. Writes top-level `assignments[taskId]` sidecar. Operator must send once per task on multi-task work items (loop-back through `confirm_assignment`). |

### Step 3: Append a changelog entry
**File:** `docs/api-spec.md`
**Action:** Modify

At the bottom of the file's changelog block (per the project's documentation discipline), append:

```markdown
- **2026-05-14 (IMP-004):** Added `assignment-confirmed` signal contract for the manual lifecycle variant. Pauses before `assign_task` (T5); payload `{ assignee, taskId? }`; persists to top-level `assignments[taskId]` sidecar in `RunMemory.data`. Variant-only — `lifecycle-agent@0.3.0` is unaffected.
```

### Step 4: Update the variants paragraph in `CLAUDE.md`
**File:** `CLAUDE.md`
**Action:** Modify

Locate the bullet starting `**Lifecycle agent variants are peers under distinct `agent_ref`s.**` in the Patterns to Follow section. The bullet currently lists current variants. Add one sentence:

> The manual variant carries a variant-specific top-level `assignments[taskId]` sidecar in `RunMemory.data` (written by `confirm_assignment`'s memory-patch builder); v0.3.0 memory is byte-unchanged.

Keep the rest of the bullet intact — this is an additive clarification, not a rewrite.

### Step 5: Verify no other docs need touching
**File:** N/A
**Action:** Verify

Confirm by reading:
- `docs/data-model.md`: no edit — the `RunMemory` entity is unchanged at the SQL level; only its JSON payload grows a new key.
- `docs/ARCHITECTURE.md`: no edit — no new component, no new module boundary.
- `docs/ui-specification.md`: N/A (no UI in v1).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `docs/api-spec.md` | Modify | One table row + one changelog entry. |
| `CLAUDE.md` | Modify | One sentence appended to the variants pattern bullet. |

## Edge Cases & Risks
- **Column alignment in the markdown table:** match the existing four rows exactly. If the table uses inline-code formatting for signal names (`` `brief-confirmed` ``), do the same.
- **Changelog format:** follow whatever format the existing entries use. If entries are dated and prefixed with the IMP/FEAT id, mirror that.
- **CLAUDE.md edit risk:** the variants bullet is long and dense. Add the sentence at the end of the bullet's prose, before any sub-bullets — don't break the existing rhythm. Re-read after editing to confirm the bullet still reads coherently.
- **Doc-first discipline:** this task lands as part of the same PR as T-305/T-306/T-307/T-308/T-309 (per the project's "include doc updates in the same PR" rule). Do NOT split into a follow-on PR.

## Acceptance Verification
- [ ] `docs/api-spec.md` signal table has a row for `assignment-confirmed` with the same column layout as the four sibling rows.
- [ ] Changelog entry appended dated 2026-05-14, referencing IMP-004.
- [ ] `CLAUDE.md` variants paragraph mentions the `assignments` sidecar.
- [ ] No edits to `docs/data-model.md` (LifecycleMemory is not a DB entity).
- [ ] Reading the updated `docs/api-spec.md` end-to-end, the new row is the only signal-table change.
- [ ] Reading the updated `CLAUDE.md` variants bullet, the additive sentence integrates without breaking the surrounding prose.
