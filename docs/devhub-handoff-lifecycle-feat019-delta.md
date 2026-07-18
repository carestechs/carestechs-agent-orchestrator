# DevHub Handoff — FEAT-019 Delta

**Date:** 2026-07-17  
**Covers:** FEAT-019 additions to `lifecycle-agent@0.6.0-human`  
**Audience:** DevHub team — what specifically changed vs. the FEAT-018 handoff  
**Full reference:** `docs/devhub-handoff-lifecycle-v06-human.md`

---

## What FEAT-019 added (short list)

1. **Two new human checkpoints** — `confirm_task_review` and `confirm_docs_update`.
2. **Six artifact-commit nodes** — commit markdown files to GitHub after key approvals.
3. **Three new `tasks-confirmed` fields** — `workflow`, `dependencies`, `filesToModify`.
4. **Two new signal names** — `tasks-reviewed` and `docs-update-confirmed`.

Everything from the FEAT-018 handoff is unchanged. The additions are purely additive.

---

## Change 1 — `confirm_task_review` checkpoint

### Where it sits in the flow

```
confirm_tasks (operator authors task list)
  ↓  [approve]
confirm_task_review  ← NEW dispatched step
  ↓  [approve]
commit_tasks (optional artifact commit)
  ↓
propose_tasks (engine fanout)
```

On reject at `confirm_task_review`, the flow loops back to `confirm_tasks` (not to `confirm_task_review` itself). The author must revise and re-submit the task list before the reviewer sees it again.

### `nodeInputs` shape

```json
{
  "tasks": [
    {
      "id": "T-001",
      "title": "Add GET /health route",
      "kind": "feature",
      "complexity": "small",
      "workflow": "standard",
      "dependsOn": [],
      "filesHint": ["src/app/main.py"]
    }
  ],
  "priorFeedback": "T-001 should be split — route and test are separate."
}
```

`priorFeedback` is `null` on first visit; populated with the reviewer's last `feedback` string on retries.

### Signal

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
    "feedback": "AC coverage is missing on T-002."
  }
}
```

`verdict` defaults to `"approve"`. `feedback` is optional on approve (ignored), required on reject by convention (the UI should prompt for it).

---

## Change 2 — `confirm_docs_update` checkpoint

### Where it sits in the flow

```
mark_task_done (all tasks complete)
  ↓  [tasks_remaining=false]
confirm_docs_update  ← NEW dispatched step
  ↓  [approve]
log_run_completed (optional event log)
  ↓
close_work_item (engine W4+W6)
```

This is the definition-of-done gate. The operator confirms that all artifact files are committed and documentation is in sync before the work item is closed in the engine.

On reject, the flow holds at `confirm_docs_update` for a re-check (does not loop to any earlier step).

### `nodeInputs` shape

```json
{
  "workItem": {
    "id": "FEAT-300",
    "title": "Health check endpoint",
    "type": "FEAT",
    "summary": "...",
    "acceptanceCriteria": ["..."]
  },
  "tasks": [ { ...LifecycleTask fields... } ],
  "completedTaskIds": ["T-001", "T-002"],
  "priorFeedback": null
}
```

### Signal

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

---

## Change 3 — Extended `tasks-confirmed` payload

Three new optional fields per task in the `tasks-confirmed` payload:

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `workflow` | `"standard" \| "mockup-first" \| "investigation-first"` | `"mockup-first"` if `kind="mockup"`, else `"standard"` | Workflow hint stored in memory and committed to the task list artifact. No routing effect. |
| `dependencies` | `string[]` | `[]` | Other task IDs this task depends on. Informational only. |
| `filesToModify` | `string[]` | `[]` | File paths expected to change. Committed to task list artifact. |

These fields are optional and backward-compatible. Existing payloads that omit them continue to work.

**Example with all FEAT-019 fields:**
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
        "dependencies": [],
        "filesToModify": ["src/app/main.py", "tests/test_health.py"]
      },
      {
        "id": "T-002",
        "title": "Update Docker healthcheck",
        "kind": "feature",
        "complexity": "small",
        "workflow": "standard",
        "dependencies": ["T-001"],
        "filesToModify": ["docker-compose.prod.yml"]
      }
    ]
  }
}
```

---

## Change 4 — Artifact commit nodes

Six new `LocalExecutor` nodes appear in the step stream. They all complete immediately. When `GITHUB_PAT` is not configured (or `codeSource.repo` is absent from run intake), each returns `{"skipped": true, "reason": "..."}` — **they are never blockers**.

### Commit nodes

| Node | Trigger | Artifact | Repo path |
|------|---------|----------|-----------|
| `log_run_started` | After `start`, before `confirm_brief` | Event log line (`started`) | `metrics/events.ndjson` |
| `commit_brief` | After `register_work_item` | Work-item brief markdown | `docs/work-items/{ID}-{slug}.md` |
| `commit_tasks` | After `confirm_task_review` approve | Task list markdown | `tasks/{ID}-tasks.md` |
| `commit_plan` | After `confirm_plan` approve | Implementation plan (per task) | `plans/plan-{TASK_ID}-{slug}.md` |
| `commit_review` | After `human_review_implementation` pass | Implementation review (per task) | `tasks/{TASK_ID}-implementation-review.md` |
| `log_run_completed` | After `confirm_docs_update` approve | Event log line (`completed`) | `metrics/events.ndjson` |

### Dispatch result shapes

**Artifact committed (PAT + codeSource present):**
```json
{ "commitSha": "a3f9c12e...", "path": "plans/plan-T-001-add-health-route.md" }
```

**Skipped (no PAT or no codeSource):**
```json
{ "skipped": true, "reason": "GITHUB_PAT not configured" }
```

### Required run-intake fields for artifact commits to activate

```json
{
  "codeSource": {
    "repo": "carestechs/carestechs-agent-orchestrator",
    "baseBranch": "main"
  }
}
```

`codeSource` is part of the existing `IMP-005` contract and is already supported in the run-start UI. No new intake field needed beyond wiring it through.

**Server-side env vars:**
- `GITHUB_PAT` — classic PAT with `repo` scope.
- `GITHUB_ARTIFACT_BRANCH` — branch to commit to (default `"main"`).

---

## Change 5 — Event log (`metrics/events.ndjson`)

Two event log lines are appended to `metrics/events.ndjson` in the project repo per run (when PAT is configured): one at `log_run_started` and one at `log_run_completed`. Additional `artifact_committed` entries are appended as a side-effect of each `commit_*` node.

Line format (NDJSON — one JSON object per line):
```json
{
  "ts": "2026-07-17T20:41:00.123456+00:00",
  "run_id": "019f71b4-bbfe-7115-8ffe-534486e0346a",
  "agent_ref": "lifecycle-agent@0.6.0-human",
  "step": "start",
  "event": "started",
  "work_item_id": "FEAT-300",
  "task_id": null,
  "detail": null
}
```

DevHub can optionally surface a link to `metrics/events.ndjson` in the run detail page as a lightweight audit trail.

---

## Updated full step stream (single-task run)

Steps marked `[opt]` require `GITHUB_PAT` + `codeSource` — they complete instantly either way.

```
step: log_run_started          ← completed  [opt]
step: confirm_brief            ← dispatched
  signal: brief-confirmed
step: confirm_brief            ← completed
step: register_work_item       ← completed
step: commit_brief             ← completed  [opt]
step: confirm_tasks            ← dispatched
  signal: tasks-confirmed
step: confirm_tasks            ← completed
step: confirm_task_review      ← dispatched  ← NEW
  signal: tasks-reviewed
step: confirm_task_review      ← completed   ← NEW
step: commit_tasks             ← completed  [opt]
step: propose_tasks            ← completed
step: confirm_assignment       ← dispatched
  signal: assignment-confirmed
step: confirm_assignment       ← completed
step: assign_task              ← completed
step: confirm_plan             ← dispatched
  signal: plan-confirmed
step: confirm_plan             ← completed
step: commit_plan              ← completed  [opt]
step: approve_plan             ← completed
step: request_implementation   ← dispatched
  signal: implementation-complete
step: request_implementation   ← completed
step: submit_implementation    ← completed
step: human_review_implementation ← dispatched
  signal: review-completed
step: human_review_implementation ← completed
step: commit_review            ← completed  [opt]
step: approve_review           ← completed
step: mark_task_done           ← completed
step: confirm_docs_update      ← dispatched  ← NEW
  signal: docs-update-confirmed
step: confirm_docs_update      ← completed   ← NEW
step: log_run_completed        ← completed  [opt]
step: close_work_item          ← completed
── run status: completed, stop_reason: done_node ──
```

**FEAT-018 step count:** 16 (excluding `confirm_mockup` branch)  
**FEAT-019 step count:** 22 (same exclusion; +6 new nodes)

---

## DevHub checklist — FEAT-019 only

- [ ] **`confirm_task_review` step** — add a new dispatched-step UI:
  - Show read-only task table with all FEAT-019 fields (kind, complexity, workflow, dependencies, files).
  - Show `priorFeedback` banner when non-null ("Reviewer previously noted: …").
  - Approve/reject buttons; reject requires feedback input.
  - Signal: `tasks-reviewed`.

- [ ] **`confirm_docs_update` step** — add a new dispatched-step UI:
  - Show work-item summary, task count, and `completedTaskIds` list.
  - Show `priorFeedback` banner when non-null.
  - Frame as "Definition of done" checklist with approve/reject.
  - Signal: `docs-update-confirmed`.

- [ ] **`tasks-confirmed` editor** — add three fields per task row:
  - `workflow` dropdown (standard / mockup-first / investigation-first) — pre-fill from `kind`.
  - `dependencies` multi-input (other task IDs).
  - `filesToModify` text area (file paths, one per line).

- [ ] **Commit node results** — in the run timeline:
  - Committed: show compact badge `committed: {path}@{sha[:7]}`.
  - Skipped: show gray neutral indicator. Tooltip: "No GITHUB_PAT configured."

- [ ] **Register new signal names** in the signal-send dropdown / autocomplete:
  - `tasks-reviewed` (for `confirm_task_review` step)
  - `docs-update-confirmed` (for `confirm_docs_update` step)
