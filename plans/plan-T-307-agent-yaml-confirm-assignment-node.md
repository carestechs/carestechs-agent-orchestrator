# Implementation Plan: T-307 — Insert `confirm_assignment` node in `lifecycle-agent@0.4.0-manual.yaml`

## Task Reference
- **Task ID:** T-307
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** The YAML is the agent's source of truth for the flow graph. Executor coverage at lifespan startup confirms the new node has a binding (T-306).

## Overview
Insert one new node declaration and edit two transitions in `agents/lifecycle-agent@0.4.0-manual.yaml`. Also update the top-of-file header comment block to list `assignment-confirmed` alongside the four existing signals. Pure YAML edit — no code changes.

## Implementation Steps

### Step 1: Update the top-of-file header comment
**File:** `agents/lifecycle-agent@0.4.0-manual.yaml`
**Action:** Modify

Locate the "Signal contracts" comment (lines ~10-14):

```yaml
# Signal contracts (documented in docs/api-spec.md):
#   - `brief-confirmed`       — payload optional (workItem corrections)
#   - `tasks-confirmed`       — payload optional (replacement task list)
#   - `plan-confirmed`        — payload optional (replacement plan markdown)
#   - `review-completed`      — payload required (verdict + optional feedback)
```

Insert `assignment-confirmed` between `tasks-confirmed` and `plan-confirmed` (matching flow order):

```yaml
# Signal contracts (documented in docs/api-spec.md):
#   - `brief-confirmed`        — payload optional (workItem corrections)
#   - `tasks-confirmed`        — payload optional (replacement task list)
#   - `assignment-confirmed`   — payload required (assignee for current task)
#   - `plan-confirmed`         — payload optional (replacement plan markdown)
#   - `review-completed`       — payload required (verdict + optional feedback)
```

Realign the dashes for readability (the column shift adds one character).

Update the prose comment at the top describing the variant (lines ~3-7) — change "four human checkpoints inserted at the LLM→engine seams (brief, tasks, plan)" to "five human checkpoints inserted at the LLM→engine seams (brief, tasks, assignment, plan)". Update the `description:` block field (lines ~25-31) symmetrically.

### Step 2: Insert the new node declaration
**File:** `agents/lifecycle-agent@0.4.0-manual.yaml`
**Action:** Modify

After the `propose_tasks:` node block and before the `assign_task:` node block, insert:

```yaml
  - name: confirm_assignment
    description: >
      Pause for `assignment-confirmed` before firing T5 (assigning → planning)
      on the current task. The operator reads the task list from
      `LifecycleMemory.tasks` and supplies `signal.payload.assignee` (required,
      non-empty) and optional `signal.payload.taskId` (defaults to
      `current_task_id`). The assignee is persisted to the top-level
      `assignments[taskId]` sidecar (variant-specific — not present in
      v0.3.0 memory). T5 itself is unchanged; this checkpoint gates it.
    inputSchema:
      type: object
      properties:
        taskId: {type: string}
```

The `inputSchema` mirrors `assign_task`'s schema — operator may supply `taskId` to override the current task. `required:` is omitted because the field is optional (the builder resolves from memory when absent).

### Step 3: Edit the transitions block
**File:** `agents/lifecycle-agent@0.4.0-manual.yaml`
**Action:** Modify

Locate the transitions block (lines starting `flow.transitions:`). Find:

```yaml
    propose_tasks: [assign_task]
    assign_task: [generate_plan]
```

Replace with:

```yaml
    propose_tasks: [confirm_assignment]
    confirm_assignment: [assign_task]
    assign_task: [generate_plan]
```

Insert in declaration order — do not reorder other transitions.

### Step 4: Verify intake schema, terminal nodes, budget unchanged
**File:** `agents/lifecycle-agent@0.4.0-manual.yaml`
**Action:** Verify

No edits — the `intakeSchema`, `terminalNodes`, and `defaultBudget` blocks must be byte-identical to before. Inspect after the edit to confirm.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `agents/lifecycle-agent@0.4.0-manual.yaml` | Modify | Header comment + one new node + two transition lines. |

## Edge Cases & Risks
- **Coverage validator boot-fails if T-306 didn't land first:** intentional — the validator catches the mistake at startup with a clear error. Land in order.
- **Loop-back behavior:** `mark_task_done` → `assign_task` (when `tasks_remaining` is true) now transparently passes through `confirm_assignment` because `mark_task_done` transitions to `assign_task`. Wait — actually that's still direct. Inspect: `mark_task_done: branch ... "true": assign_task`. With the new transitions, `mark_task_done` jumps directly to `assign_task` (skipping `confirm_assignment`!). **Fix in this step:** also edit `mark_task_done`'s branch target to point to `confirm_assignment` instead of `assign_task`. Without this fix, the second task in a multi-task run skips the human checkpoint — defeating the IMP.
- **Correction:** the transitions edit must include:

  ```yaml
      mark_task_done:
        branch:
          rule: tasks_remaining
          "true": confirm_assignment
          "false": close_work_item
  ```

  Update Step 3 to also touch `mark_task_done`'s branch. The IMP brief §4 calls out "operator confirms the assignee for each task individually" — this is the structural change that achieves it.
- **v0.3.0 + v0.2.0 YAMLs untouched:** verify by `git diff agents/`.
- **YAML indentation:** the file uses 2-space indent under `nodes:` (each node prefixed with `- name:`). Match exactly.

## Acceptance Verification
- [ ] `nodes:` list includes `confirm_assignment` with descriptive comment matching the style of `confirm_brief`/`confirm_tasks`/`confirm_plan`.
- [ ] `flow.transitions` has `propose_tasks: [confirm_assignment]` and `confirm_assignment: [assign_task]`.
- [ ] `flow.transitions.mark_task_done.branch."true"` points to `confirm_assignment` (multi-task loop-back routes through the new checkpoint).
- [ ] Top-of-file comment block lists `assignment-confirmed` and prose says "five human checkpoints".
- [ ] `intakeSchema`, `terminalNodes`, `defaultBudget` unchanged.
- [ ] `agents/lifecycle-agent@0.3.0.yaml`, `agents/lifecycle-agent@0.2.0.yaml`, `agents/lifecycle-agent@0.1.0.yaml` byte-unchanged (`git diff agents/lifecycle-agent@0.{1,2,3}.0.yaml`).
- [ ] `uv run uvicorn app.main:app` boots cleanly — executor coverage validator confirms binding presence.
