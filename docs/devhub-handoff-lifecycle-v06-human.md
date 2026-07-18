# DevHub Handoff — lifecycle-agent@0.6.0-human

**Date:** 2026-07-17 (updated for FEAT-019)  
**Covers:** FEAT-018 (fully human-input lifecycle variant) + FEAT-019 (artifact commits, extra checkpoints)  
**Agent ref:** `lifecycle-agent@0.6.0-human`  
**Previous agent ref:** `lifecycle-agent@0.5.0-manual`

---

## Summary

`lifecycle-agent@0.6.0-human` is a standalone lifecycle variant where the orchestrator makes **zero LLM or platform calls**. Every content-producing step — the work-item brief, task list, HTML mockup, and implementation plan — is supplied by the operator via signal payloads. The orchestrator handles only state transitions, engine wiring, memory, and (when configured) artifact commits to the project repo.

The intended workflow: operator runs Claude Code (or any tool) externally to produce artefacts, then pastes them into DevHub's signal payloads. FEAT-019 extends this with two new review checkpoints and optional GitHub artifact commits.

### Flow comparison

| Step | @0.5.0-manual | @0.6.0-human |
|------|--------------|--------------|
| Brief | `load_work_item` (LLM) → `confirm_brief` (review) | `confirm_brief` (operator authors) → `commit_brief` (optional) |
| Task list | `generate_tasks` (LLM) → `confirm_tasks` (review) | `confirm_tasks` (author) → `confirm_task_review` → `commit_tasks` (optional) |
| Mockup | `generate_mockup` (LLM) → `confirm_mockup` (review) | `confirm_mockup` (operator pastes HTML) |
| Plan | `generate_plan` (LLM) → `advance_plan` → `confirm_plan` (review) | `confirm_plan` (author) → `commit_plan` (optional) |
| Review | `human_review_implementation` | `human_review_implementation` → `commit_review` (optional) |
| Close | `close_work_item` | `confirm_docs_update` → `close_work_item` |

---

## What changed for DevHub — FEAT-018

### 1. The step stream no longer contains generate/load nodes

The following step names will **never appear** in a `@0.6.0-human` run trace:
- `load_work_item`
- `generate_tasks`
- `generate_mockup`
- `generate_plan`
- `advance_plan`

DevHub should not wait for or gate on these steps. The run jumps directly from `log_run_started` to `confirm_brief` (dispatched).

### 2. `confirm_brief` — operator is the author, not the reviewer

In prior variants `confirm_brief` fires after `load_work_item` has already populated the brief from the LLM. In `@0.6.0-human`, `RunMemory` is empty when `confirm_brief` fires. **The operator must supply the full brief in the payload.**

`workItem.id`, `workItem.title`, and `workItem.type` are **required** in the `brief-confirmed` payload. Omitting any of them causes the dispatch to fail and the run terminates with `stop_reason=error`.

### 3. `brief-confirmed` payload is extended (also applies to @0.5.0-manual)

Two new optional fields are accepted on `workItem` in all manual variants:

| New field | Type | Purpose |
|-----------|------|---------|
| `workItem.summary` | string | Full brief text / problem statement |
| `workItem.acceptanceCriteria` | string[] | List of acceptance criteria |

In `@0.5.0-manual`, these fields merge on top of the LLM-authored values. In `@0.6.0-human`, they are the only source and should be populated.

### 4. `confirm_tasks` — operator supplies the task list from scratch

Same signal (`tasks-confirmed`), extended payload shape (see FEAT-019 section below). The `tasks[].kind` field gates the mockup branch. Since no `generate_tasks` step runs, `nodeInputs.tasks` will be an empty array. DevHub should present a blank task-authoring UI.

### 5. `confirm_mockup` — operator supplies the HTML (not reviews LLM output)

When a task has `kind="mockup"`, the flow routes through `confirm_mockup`. The difference is that `nodeInputs.mockupHtml` will be **empty string or absent** (no `generate_mockup` ran). The operator pastes the HTML they generated externally into the `mockupHtml` field of the approve payload.

### 6. `confirm_plan` — operator supplies the plan from scratch

`nodeInputs` at `confirm_plan` will have no prior plan content. DevHub should show an empty textarea rather than a review UI.

### 7. `approve_plan` chains T6+T7 (no separate `advance_plan`)

`approve_plan` chains both T6 and T7 via `SequenceEngineExecutor`. From DevHub's perspective this is invisible — `approve_plan` simply takes slightly longer to complete.

---

## What changed for DevHub — FEAT-019

### 1. Two new human checkpoints

| Node | Signal | Position in flow |
|------|--------|-----------------|
| `confirm_task_review` | `tasks-reviewed` | After `confirm_tasks` approve, before `commit_tasks` + `propose_tasks` |
| `confirm_docs_update` | `docs-update-confirmed` | After all tasks done, before `log_run_completed` + `close_work_item` |

Both follow the same `checkpoint_approved` predicate: `verdict="approve"` advances the flow; `verdict="reject"` holds or loops with feedback.

### 2. `confirm_task_review` — independent review gate

After the operator authors the task list at `confirm_tasks`, a reviewer (may be the same operator in solo setups) inspects the list for completeness, correct kinds/complexities, and AC coverage.

`nodeInputs` at this checkpoint:
```json
{
  "tasks": [ { ...LifecycleTask fields... } ],
  "priorFeedback": "string | null"
}
```

`priorFeedback` is populated on retry if the reviewer previously rejected (from `rejections["confirm_task_review"]` in memory). DevHub should display it prominently so the author knows what to fix.

On reject: the flow loops back to `confirm_tasks` (not back to `confirm_task_review`) — the author must revise and re-submit the task list before the reviewer sees it again.

### 3. `confirm_docs_update` — definition-of-done gate

After all tasks complete, the operator confirms that all artifact files (brief, task list, plans, reviews) are committed to the project repo and documentation is up to date.

`nodeInputs` at this checkpoint:
```json
{
  "workItem": { ...WorkItemRef fields... },
  "tasks": [ { ...LifecycleTask fields... } ],
  "completedTaskIds": ["T-001", "T-002"],
  "priorFeedback": "string | null"
}
```

On approve: the flow advances to `log_run_completed` then `close_work_item`. On reject: holds at `confirm_docs_update` for a re-check.

### 4. `tasks-confirmed` payload extended (FEAT-019 fields)

Three new optional per-task fields:

| New field | Type | Purpose |
|-----------|------|---------|
| `tasks[].workflow` | `"standard" \| "mockup-first" \| "investigation-first"` | Explicit workflow hint for the task. Defaults to `"mockup-first"` if `kind="mockup"`, else `"standard"`. |
| `tasks[].dependencies` | string[] | Sibling task IDs this task depends on. Surfaced in artifact commits. |
| `tasks[].filesToModify` | string[] | File paths expected to change. Surfaced in the committed task list. |

These fields are purely informational in the current release — they are committed to the task list markdown artifact and stored in `RunMemory.tasks[].workflow / .depends_on / .files_hint`, but do not affect flow routing.

### 5. Optional GitHub artifact commits

When `GITHUB_PAT` and `codeSource` are configured on the run, the orchestrator automatically commits markdown artifacts to the project repo at key flow points:

| Node | Artifact committed | Path in repo |
|------|--------------------|-------------|
| `commit_brief` | Work-item brief | `docs/work-items/{ID}-{slug}.md` |
| `commit_tasks` | Task list | `tasks/{ID}-tasks.md` |
| `commit_plan` | Implementation plan (per task) | `plans/plan-{TASK_ID}-{slug}.md` |
| `commit_review` | Implementation review (per task) | `tasks/{TASK_ID}-implementation-review.md` |
| `log_run_started` | Event log entry (`started`) | `metrics/events.ndjson` |
| `log_run_completed` | Event log entry (`completed`) | `metrics/events.ndjson` |

All commit nodes return `{"commitSha": "...", "path": "..."}` in their dispatch result (surfaced in the trace). When `GITHUB_PAT` is absent, each node returns `{"skipped": true, "reason": "GITHUB_PAT not configured"}` and the flow continues normally — **these nodes are never blockers**.

**Required config:**
- `GITHUB_PAT` env var — classic PAT with `repo` scope.
- `codeSource` in run intake — `{"repo": "owner/repo", "baseBranch": "main"}`.
- `GITHUB_ARTIFACT_BRANCH` env var (optional) — branch to commit to. Defaults to `"main"`.

DevHub should surface the `commitSha` and `path` from commit node results in the run timeline so operators can see what was committed.

---

## Signal contract — @0.6.0-human

### `brief-confirmed` ← authoring mode

```json
{
  "name": "brief-confirmed",
  "payload": {
    "workItem": {
      "id": "FEAT-300",
      "title": "Health check endpoint",
      "type": "FEAT",
      "summary": "Add GET /health returning {\"status\":\"ok\"}, HTTP 200, no auth.",
      "acceptanceCriteria": [
        "GET /health returns HTTP 200 with body {\"status\": \"ok\"}",
        "No Authorization header required",
        "Endpoint registered in the FastAPI router"
      ]
    }
  }
}
```

`workItem.id`, `workItem.title`, `workItem.type` are required. `verdict` defaults to `"approve"`. Send `verdict: "reject"` with `feedback` to loop back.

### `tasks-confirmed` ← authoring mode (FEAT-019 fields included)

```json
{
  "name": "tasks-confirmed",
  "payload": {
    "tasks": [
      {
        "id": "T-001",
        "title": "Add GET /health route",
        "kind": "feature",
        "complexity": "small",
        "workflow": "standard",
        "description": "Register /health in main.py. Return {status: ok}, HTTP 200.",
        "dependencies": [],
        "filesToModify": ["src/app/main.py", "tests/test_health.py"]
      },
      {
        "id": "T-002",
        "title": "Update Docker healthcheck",
        "kind": "feature",
        "complexity": "small",
        "workflow": "standard",
        "description": "Point HEALTHCHECK in docker-compose.prod.yml at /health.",
        "dependencies": ["T-001"],
        "filesToModify": ["docker-compose.prod.yml"]
      }
    ]
  }
}
```

`kind` values: `"feature"` (default) | `"mockup"` | `"bug"` | `"chore"`.  
`kind="mockup"` tasks route through `confirm_mockup` after assignment.

### `tasks-reviewed` ← **new in FEAT-019**

```json
{
  "name": "tasks-reviewed",
  "payload": {
    "verdict": "approve"
  }
}
```

```json
{
  "name": "tasks-reviewed",
  "payload": {
    "verdict": "reject",
    "feedback": "T-001 should be split: route registration and test coverage are separate concerns."
  }
}
```

On reject: the flow routes back to `confirm_tasks`. The reviewer's feedback is stored and surfaces as `priorFeedback` on the next `confirm_task_review` visit.

### `mockup-approved` ← operator supplies HTML

```json
{
  "name": "mockup-approved",
  "taskId": "T-001",
  "payload": {
    "verdict": "approve",
    "mockupHtml": "<!DOCTYPE html><html>...<body>...</body></html>"
  }
}
```

### `plan-confirmed` ← authoring mode

```json
{
  "name": "plan-confirmed",
  "taskId": "T-001",
  "payload": {
    "verdict": "approve",
    "plan": "# Plan: Add GET /health\n\n1. Add `/health` route to `src/app/main.py`.\n2. Return `JSONResponse({\"status\":\"ok\"}, status_code=200)`."
  }
}
```

### `docs-update-confirmed` ← **new in FEAT-019**

```json
{
  "name": "docs-update-confirmed",
  "payload": {
    "verdict": "approve"
  }
}
```

```json
{
  "name": "docs-update-confirmed",
  "payload": {
    "verdict": "reject",
    "feedback": "Plan for T-002 was not committed to the repo yet."
  }
}
```

On reject: holds at `confirm_docs_update` for another check. The feedback surfaces as `priorFeedback` on the next visit.

### `assignment-confirmed`, `implementation-complete`, `review-completed`

Unchanged from `@0.5.0-manual`.

---

## Full signal reference — @0.6.0-human

| Checkpoint node | Signal name | `taskId` | Required payload | Notes |
|-----------------|-------------|----------|-----------------|-------|
| `confirm_brief` | `brief-confirmed` | no | `workItem.id`, `workItem.title`, `workItem.type` | Author mode. Optional `summary`, `acceptanceCriteria`. |
| `confirm_tasks` | `tasks-confirmed` | no | `tasks[]` with `id` + `title` | Author mode. Optional `kind`, `complexity`, `description`, `workflow`, `dependencies`, `filesToModify`. |
| `confirm_task_review` | `tasks-reviewed` | no | `verdict: "approve"\|"reject"` | **New (FEAT-019).** Reject loops to `confirm_tasks`. Optional `feedback`. |
| `confirm_assignment` | `assignment-confirmed` | yes | `assignee: string` | Unchanged from @0.5.0-manual. |
| `confirm_mockup` | `mockup-approved` | yes | `mockupHtml` on approve | Operator supplies HTML. On reject: `feedback`. |
| `confirm_plan` | `plan-confirmed` | yes | `plan: string` | Author mode. Operator supplies full plan markdown. |
| `request_implementation` | `implementation-complete` | yes | none | Optional `prUrl`, `commitSha`, `summary`. |
| `human_review_implementation` | `review-completed` | yes | `verdict: "pass"\|"fail"` | Optional `feedback` on fail. |
| `confirm_docs_update` | `docs-update-confirmed` | no | `verdict: "approve"\|"reject"` | **New (FEAT-019).** Reject holds at this checkpoint. Optional `feedback`. |

---

## Expected step stream — single-task feature run

Steps marked `[opt]` appear in the stream but complete instantly with `skipped=true` when `GITHUB_PAT` is not configured.

```
step: log_run_started          ← completed  [opt — event log]
step: confirm_brief            ← dispatched
operator_signal: brief-confirmed
step: confirm_brief            ← completed
step: register_work_item       ← completed  (engine W1)
step: commit_brief             ← completed  [opt — brief.md commit]
step: confirm_tasks            ← dispatched
operator_signal: tasks-confirmed
step: confirm_tasks            ← completed
step: confirm_task_review      ← dispatched  (NEW — independent reviewer)
operator_signal: tasks-reviewed
step: confirm_task_review      ← completed
step: commit_tasks             ← completed  [opt — tasks.md commit]
step: propose_tasks            ← completed  (T1xN + W2 fanout)
step: confirm_assignment       ← dispatched
operator_signal: assignment-confirmed
step: confirm_assignment       ← completed
step: assign_task              ← completed  (T5)
─── kind="mockup" branch ─────────────────────────────────────────────
step: confirm_mockup           ← dispatched  (only for mockup tasks)
operator_signal: mockup-approved
step: confirm_mockup           ← completed
─── kind≠"mockup" branch (continues) ────────────────────────────────
step: confirm_plan             ← dispatched
operator_signal: plan-confirmed
step: confirm_plan             ← completed
step: commit_plan              ← completed  [opt — plan-{id}.md commit]
step: approve_plan             ← completed  (T6+T7 chain)
step: request_implementation   ← dispatched
operator_signal: implementation-complete
step: request_implementation   ← completed
step: submit_implementation    ← completed  (T9)
step: human_review_implementation ← dispatched
operator_signal: review-completed
step: human_review_implementation ← completed
step: commit_review            ← completed  [opt — review.md commit]
step: approve_review           ← completed  (T10)
step: mark_task_done           ← completed
─── all tasks done ────────────────────────────────────────────────────
step: confirm_docs_update      ← dispatched  (NEW — docs gate)
operator_signal: docs-update-confirmed
step: confirm_docs_update      ← completed
step: log_run_completed        ← completed  [opt — event log]
step: close_work_item          ← completed  (W4+W6 chain)
── run status: completed, stop_reason: done_node ──────────────────────
```

Multi-task runs loop back from `mark_task_done` to `confirm_assignment` for each remaining task.

---

## DevHub implementation checklist

### FEAT-018 items (from prior handoff)

- [ ] Register `lifecycle-agent@0.6.0-human` as a known agent ref with a distinct UI label (e.g. "Human-input").
- [ ] Route `confirm_brief`, `confirm_tasks`, `confirm_plan` to authoring UIs (not review UIs) for this agent ref.
- [ ] `confirm_brief` — show `nodeInputs.workItem.content` as read-only reference; provide Title, Type, Summary, AC editor fields.
- [ ] `confirm_tasks` — show blank task-list editor; each row: ID, Title, Kind, Complexity, Description.
- [ ] `confirm_mockup` — show empty HTML paste area; render pasted HTML in sandboxed `<iframe srcdoc>` after submit.
- [ ] `confirm_plan` — show blank markdown editor with task context sidebar.
- [ ] `approve_plan` — show progress indicator (takes ~2 s for T6+T7 chain). No operator action.

### FEAT-019 items (new)

- [ ] **`confirm_task_review` step** — new dispatched step between `confirm_tasks` and `commit_tasks`.
  - Display the task list from `nodeInputs.tasks` in read-only mode for reviewer inspection.
  - Display `nodeInputs.priorFeedback` prominently if non-null (reviewer's previous objection).
  - Signal: `tasks-reviewed` with `verdict: "approve" | "reject"` and optional `feedback`.
  - On reject: inform the author that the reviewer requested revisions — the flow routes back to `confirm_tasks`.

- [ ] **`confirm_docs_update` step** — new dispatched step after all tasks complete.
  - Display the work item summary, task list, and `completedTaskIds` from `nodeInputs`.
  - Display `nodeInputs.priorFeedback` prominently if non-null.
  - Signal: `docs-update-confirmed` with `verdict: "approve" | "reject"` and optional `feedback`.
  - Frame the UI as a "definition of done" checklist: all artifacts committed, docs updated, etc.

- [ ] **`tasks-confirmed` payload** — add three new optional fields to the task-list editor:
  - `workflow` (dropdown: standard / mockup-first / investigation-first) — pre-fill based on `kind` if absent.
  - `dependencies` (multi-select or text input of other task IDs).
  - `filesToModify` (multi-line text input of file paths).

- [ ] **Commit node results** — for `commit_brief`, `commit_tasks`, `commit_plan`, `commit_review` steps in the timeline, surface the `commitSha` and `path` from the dispatch result. A compact badge like `committed: plans/plan-T-001-add-health-route.md@a3f9c12` gives operators visibility into what was auto-committed.

- [ ] **Skipped commit nodes** — when a commit node result contains `{"skipped": true}`, display it as a gray/neutral step (not an error). Add a tooltip: "Configure GITHUB_PAT to enable automatic artifact commits."

- [ ] **No LLM-wait states** — all steps are either `dispatched` (waiting for an operator signal) or `completed` within seconds. Do not show "AI is thinking…" copy for this agent ref.

---

## Backward compatibility

- `@0.3.0`, `@0.4.0-manual`, and `@0.5.0-manual` are **unchanged**. No signal contracts, node names, or `nodeInputs` shapes changed in those variants.
- `workItem.summary`, `workItem.acceptanceCriteria` on `brief-confirmed` are additive to all manual variants (optional, safe defaults).
- `tasks[].kind` on `tasks-confirmed` was introduced in FEAT-017 and already accepted by `@0.5.0-manual`.
- FEAT-019 fields (`workflow`, `dependencies`, `filesToModify`) are also additive — all optional, safe-default to empty lists. DevHub can send them from `@0.6.0-human` onward without an orchestrator-side guard.
