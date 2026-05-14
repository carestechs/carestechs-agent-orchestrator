# Implementation Plan: T-298 — New agent YAML `lifecycle-agent@0.4.0-manual.yaml`

## Task Reference
- **Task ID:** T-298
- **Type:** Backend (configuration)
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** FEAT-015 §4.1 — the variant's flow graph is the contract between FlowResolver and the executor registry. YAML lands first; bindings (T-299) load against it.

## Overview
Copy `agents/lifecycle-agent@0.3.0.yaml` to `agents/lifecycle-agent@0.4.0-manual.yaml`. Insert four new nodes (`confirm_brief`, `confirm_tasks`, `confirm_plan`, `human_review_implementation`). Modify `flow.transitions` to thread the new nodes into the linear flow per the FEAT-015 §4.1 diff. Remove the `review_implementation` LLM node entry (replaced by `human_review_implementation`). Update `ref`, `version`, and the header comment block; leave everything else byte-identical to v0.3.0.

## Implementation Steps

### Step 1: Copy the v0.3.0 YAML as a starting point
**File:** `agents/lifecycle-agent@0.4.0-manual.yaml`
**Action:** Create

```bash
cp agents/lifecycle-agent@0.3.0.yaml agents/lifecycle-agent@0.4.0-manual.yaml
```

### Step 2: Rewrite the header comment
**File:** `agents/lifecycle-agent@0.4.0-manual.yaml`
**Action:** Modify

Replace the top comment block with:

```yaml
# lifecycle-agent@0.4.0-manual — FEAT-015
#
# Manual variant of the lifecycle: same eight-stage skeleton as v0.3.0
# with four human checkpoints inserted at the LLM→engine seams (brief,
# tasks, plan) and a human reviewer replacing the LLM `review_implementation`
# node. Operator drives every commit to engine state; LLM still
# synthesises artefacts but cannot advance the flow without explicit
# operator approval via the `/api/v1/runs/{id}/signals` endpoint.
#
# Signal contracts:
#   - `brief-confirmed`        — payload optional (workItem corrections)
#   - `tasks-confirmed`        — payload optional (replacement task list)
#   - `plan-confirmed`         — payload optional (replacement plan markdown)
#   - `review-completed`       — payload required (verdict + optional feedback)
#
# Bootstrap wiring: `register_lifecycle_v04_manual` in
# `src/app/modules/ai/executors/bootstrap.py`. Reuses every v0.3.0
# binding under this agent_ref + adds four HumanExecutor bindings.
#
# Memory shape, engine workflows, intake schema, and terminal nodes are
# byte-identical to v0.3.0.
```

### Step 3: Update `ref` / `version` / `description`
**File:** `agents/lifecycle-agent@0.4.0-manual.yaml`
**Action:** Modify

```yaml
ref: lifecycle-agent@0.4.0-manual
version: "0.4.0-manual"
description: >
  Manual variant of the lifecycle. Same eight-stage skeleton as v0.3.0,
  with four human checkpoints inserted at the LLM→engine seams (brief,
  tasks, plan) and a human reviewer replacing the LLM review step.
  Operator drives every commit to engine state; LLM still synthesises
  artefacts but cannot advance the flow without explicit approval.
```

### Step 4: Insert four new `nodes`
**File:** `agents/lifecycle-agent@0.4.0-manual.yaml`
**Action:** Modify

Insert these four node entries into the `nodes:` list (positions are illustrative; the loader doesn't require ordering, but keep them adjacent to their semantic neighbors for readability):

```yaml
  - name: confirm_brief
    description: >
      Pause for an operator-injected `brief-confirmed` signal before
      committing W1. The operator reviews `LifecycleMemory.work_item`
      (id, kind, title) and either approves or sends corrections in the
      signal payload — `signal.payload.workItem` (optional) overwrites
      the memory record before the next node fires.
    inputSchema:
      type: object

  - name: confirm_tasks
    description: >
      Pause for `tasks-confirmed` before fanning out to the engine. The
      operator reviews `LifecycleMemory.tasks` (list of {id, title,
      summary}) and may rewrite the list via `signal.payload.tasks`
      (optional). The replacement is persisted to memory and
      `propose_tasks` then commits whatever is in memory.
    inputSchema:
      type: object

  - name: confirm_plan
    description: >
      Pause for `plan-confirmed` after the LLM authored the plan and
      fired T6 (planning → plan_review). The engine task sits at
      `plan_review`; the operator reads the plan from
      `LifecycleMemory.taskPlans[<taskId>]` and either approves or
      rewrites it via `signal.payload.plan` (optional).
    inputSchema:
      type: object
      properties:
        taskId: {type: string}
      required: [taskId]

  - name: human_review_implementation
    description: >
      Pause for `review-completed` from a human reviewer. Payload
      requires `verdict ∈ {"pass", "fail"}` and optional `feedback`
      string. Writes the same `reviewHistory[]` entry shape the LLM
      reviewer writes (shared via `_patch_review` discipline) so
      downstream `approve_review` / T10 and `correct_implementation`
      read the same memory contract as v0.3.0.
    inputSchema:
      type: object
      properties:
        taskId: {type: string}
      required: [taskId]
```

### Step 5: Remove `review_implementation` node entry
**File:** `agents/lifecycle-agent@0.4.0-manual.yaml`
**Action:** Modify

Delete the `- name: review_implementation` block. `human_review_implementation` takes its slot in the flow graph.

### Step 6: Rewrite `flow.transitions`
**File:** `agents/lifecycle-agent@0.4.0-manual.yaml`
**Action:** Modify

Replace the `transitions:` block with:

```yaml
flow:
  policy: deterministic
  entryNode: start
  transitions:
    start: [load_work_item]
    load_work_item: [confirm_brief]
    confirm_brief: [register_work_item]
    register_work_item: [generate_tasks]
    generate_tasks: [confirm_tasks]
    confirm_tasks: [propose_tasks]
    propose_tasks: [assign_task]
    assign_task: [generate_plan]
    generate_plan: [confirm_plan]
    confirm_plan: [approve_plan]
    approve_plan: [request_implementation]
    request_implementation: [submit_implementation]
    submit_implementation: [human_review_implementation]
    human_review_implementation:
      branch:
        rule: review_passed
        "true": approve_review
        "false": correct_implementation
    approve_review: [mark_task_done]
    mark_task_done:
      branch:
        rule: tasks_remaining
        "true": assign_task
        "false": close_work_item
    correct_implementation:
      branch:
        rule: correction_attempts_under_bound
        "true": request_implementation
        "false": terminate_correction_budget
    close_work_item: []
    terminate_correction_budget: []
```

### Step 7: Sanity-check `intakeSchema`, `terminalNodes`, `defaultBudget`
**File:** `agents/lifecycle-agent@0.4.0-manual.yaml`
**Action:** Verify

These three blocks MUST be byte-identical to v0.3.0. Diff to confirm:

```bash
diff <(grep -A 30 'intakeSchema:' agents/lifecycle-agent@0.3.0.yaml) <(grep -A 30 'intakeSchema:' agents/lifecycle-agent@0.4.0-manual.yaml)
```

Expected: only the YAML header / leading lines differ; the intake block, `terminalNodes`, and `defaultBudget` are identical.

### Step 8: Smoke-test the agent loader
**File:** `tests/modules/ai/test_agent_loader.py` (or wherever loader tests live)
**Action:** Verify

Run:
```bash
uv run python -c "from app.modules.ai.agents import load_agent; print(load_agent('lifecycle-agent@0.4.0-manual').flow.entry_node)"
```

Expected output: `start`. Any YAML parse error or schema-validation failure surfaces here, before T-299's bootstrap reaches `validate_executor_coverage`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `agents/lifecycle-agent@0.4.0-manual.yaml` | Create | New agent definition: 19 nodes (15 shared + 4 new), modified transitions, FEAT-015 header. |

## Edge Cases & Risks
- **Missed node in transitions.** If a node is in `nodes:` but missing from `flow.transitions`, the resolver raises `FlowDeclarationError` at run time. Verify every non-terminal node has an outgoing edge (check by cross-referencing the two blocks).
- **Existing predicate `review_passed` reads `result.verdict`.** `_apply_review_verdict` writes a `reviewHistory[]` entry but doesn't expose `verdict` on the dispatch's `result` envelope shape that the resolver consumes. Verify: `flow_predicates.review_passed` either reads from memory (good, no change needed) or from the dispatch result (then the human reviewer's envelope must surface `verdict` at the top level). Check `src/app/modules/ai/flow_predicates.py::review_passed` before merging; surface any mismatch as a follow-up note in T-302.
- **`current_task_id` requirement for `human_review_implementation`.** The YAML declares `taskId` as a required `inputSchema` field. The runtime threads `taskId` from `LifecycleMemory.current_task_id` automatically (per `runtime_deterministic.py::_execute_node` line ~246) — no operator action needed; verify the schema doesn't fight the runtime.
- **Loader caching.** Some agent loaders cache by file mtime. If the orchestrator was running when this file is created, a process restart may be needed for the new agent to register. Document for the operator running T-302.

## Acceptance Verification
- [ ] AC-1 — File exists at `agents/lifecycle-agent@0.4.0-manual.yaml`.
- [ ] AC-2 — `nodes:` contains exactly 19 entries with the names listed in T-298 task definition.
- [ ] AC-3 — `flow.transitions` matches the rewrite in Step 6.
- [ ] AC-4 — `intakeSchema`, `terminalNodes: [close_work_item, terminate_correction_budget]`, and `defaultBudget` are byte-identical to v0.3.0.
- [ ] AC-5 — Loader smoke check returns `start` for `flow.entry_node`.
- [ ] AC-6 — No `review_implementation` node entry remains in this file.
