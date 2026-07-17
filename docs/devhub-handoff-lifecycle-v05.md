# DevHub Handoff — lifecycle-agent@0.5.0-manual

**Date:** 2026-07-13  
**Covers:** FEAT-016 (PR URL in review context) + FEAT-017 (mockup task conditional flow)  
**Agent ref:** `lifecycle-agent@0.5.0-manual`  
**Previous agent ref:** `lifecycle-agent@0.4.0-manual`

---

## Summary of changes

Two features shipped since the last DevHub integration point (`@0.4.0-manual`):

| # | Feature | What DevHub must do |
|---|---------|---------------------|
| FEAT-016 | PR URL in implementation review | Collect `prUrl`/`commitSha`/`summary` when sending `implementation-complete`; render `implementationRef` at the `human_review_implementation` checkpoint |
| FEAT-017 | Conditional mockup flow | Handle a new checkpoint (`confirm_mockup`) for tasks with `kind="mockup"`; render an HTML mockup in a sandboxed iframe; send `mockup-approved` signal |

---

## FEAT-016 — PR URL in implementation review context

### What changed

`implementation-complete` now accepts an optional structured payload. When the implementer supplies a PR URL, the orchestrator persists it in `RunMemory` and surfaces it at the `human_review_implementation` checkpoint so reviewers can navigate directly to the PR.

### Signal change — `implementation-complete`

**Before (still valid — backward-compatible):**
```json
{ "name": "implementation-complete", "taskId": "T-001", "payload": {} }
```

**Now (with optional PR reference):**
```json
{
  "name": "implementation-complete",
  "taskId": "T-001",
  "payload": {
    "prUrl": "https://github.com/org/repo/pull/42",
    "commitSha": "abc123def456",
    "summary": "Adds /version endpoint; no schema changes."
  }
}
```

All three payload fields are optional. An empty `payload: {}` is backward-compatible and leaves `RunMemory` unchanged. Unknown fields return 422.

### New nodeInputs at `human_review_implementation`

When the implementer sent a PR reference, the checkpoint now includes an `implementationRef` block:

```json
{
  "currentTask": { "id": "T-001", "title": "..." },
  "implementationRef": {
    "prUrl": "https://github.com/org/repo/pull/42",
    "commitSha": "abc123def456",
    "summary": "Adds /version endpoint; no schema changes."
  }
}
```

`implementationRef` is absent when the implementer sent an empty payload. DevHub should render it conditionally — a link to the PR if `prUrl` is present, the commit SHA if only `commitSha` is present, and omit the block entirely when neither is set.

### DevHub implementation checklist — FEAT-016

- [ ] At the `request_implementation` checkpoint, expose an optional "PR reference" form: URL input (`prUrl`), SHA input (`commitSha`), summary textarea. None are required.
- [ ] Include populated fields in the `implementation-complete` payload when sending the signal.
- [ ] At `human_review_implementation`, render `nodeInputs.implementationRef` when present:
  - `prUrl` → clickable link opening in a new tab ("View PR")
  - `commitSha` → monospace badge ("Commit `abc123d`")
  - `summary` → plain text below the link/badge
- [ ] When `implementationRef` is absent, show no PR block (don't render empty state or placeholder).

---

## FEAT-017 — Conditional mockup flow

### What changed

Tasks now carry a `kind` field. When `kind = "mockup"`, the flow inserts two new nodes between `assign_task` and `generate_plan`:

```
assign_task → [branch on current_task_is_mockup]
                 │
    kind = "mockup"        kind = feature/bug/chore
         │                         │
  generate_mockup             generate_plan (unchanged)
         │
  confirm_mockup  ←── you send "mockup-approved" here
         │
    [approve]→ generate_plan
    [reject] → generate_mockup (loops, up to rejection budget)
```

Non-mockup tasks skip both new nodes entirely.

### Task kind field

Every task object in `nodeInputs.tasks` (surfaced at `confirm_tasks`) now includes `kind`:

```json
{
  "id": "T-001",
  "title": "Design login screen",
  "kind": "mockup",
  "complexity": "medium",
  "description": "...",
  "acceptanceCriteria": ["..."]
}
```

**Values:** `"feature"` (default) | `"mockup"` | `"bug"` | `"chore"`

Backward-compatible: tasks without `kind` in older run memory default to `"feature"`.

Show a visual badge on mockup tasks in the task list view at `confirm_tasks` and in per-task detail views.

### New checkpoint — `confirm_mockup`

Fires only for `kind="mockup"` tasks, after the LLM generates the HTML mockup.

#### nodeInputs shape

```json
{
  "currentTask": {
    "id": "T-001",
    "title": "Design login screen",
    "kind": "mockup",
    "description": "...",
    "acceptanceCriteria": ["..."]
  },
  "mockupHtml": "<!DOCTYPE html>...",
  "mockupDescription": "Login screen — email + password + forgot link"
}
```

`mockupHtml` is a complete, self-contained HTML document (inline CSS, no external dependencies). **Render it inside a sandboxed `<iframe srcdoc="...">` — do not inject it directly into the page DOM.**

#### Signal to send — `mockup-approved`

**Approve:**
```json
{
  "name": "mockup-approved",
  "taskId": "T-001",
  "payload": { "outcome": "approve" }
}
```

**Reject with feedback:**
```json
{
  "name": "mockup-approved",
  "taskId": "T-001",
  "payload": {
    "outcome": "reject",
    "feedback": "Needs a logo slot at the top and a larger CTA button."
  }
}
```

On rejection the orchestrator loops back to `generate_mockup` with the feedback injected into the LLM prompt. The loop repeats up to `LIFECYCLE_MAX_CHECKPOINT_REJECTIONS` times (default 3). Exhausting the budget terminates the run with `stop_reason=error`, `final_state.reason=rejection_budget_exceeded`.

### DevHub implementation checklist — FEAT-017

- [ ] Register `confirm_mockup` as a known checkpoint key with `allowedOutcomes: ["approve", "reject"]`.
- [ ] Map `checkpointKey="confirm_mockup"` → orchestrator signal `name="mockup-approved"` in `OrchestratorExecutorClient`.
- [ ] Render `nodeInputs.mockupHtml` in a sandboxed `<iframe srcdoc="...">` with `sandbox="allow-scripts allow-same-origin"`.
- [ ] Show `nodeInputs.mockupDescription` as a caption below the iframe.
- [ ] Surface a `feedback` text field when the operator selects "reject"; include it in the signal payload.
- [ ] Show a visual badge on tasks with `kind="mockup"` in the task list at `confirm_tasks` and in task detail views.
- [ ] Confirm the stream advances `currentCheckpointKey` to `confirm_mockup` only for mockup tasks; non-mockup tasks continue to `confirm_plan` after assignment.
- [ ] Test the rejection loop: reject once with feedback → run returns to `generate_mockup` → new `confirm_mockup` event arrives with updated `mockupHtml`.

---

## Full signal contract reference — lifecycle-agent@0.5.0-manual

| Checkpoint node              | Signal name              | `taskId` | Required payload fields     | Notes |
|------------------------------|--------------------------|----------|-----------------------------|-------|
| `confirm_brief`              | `brief-confirmed`        | no       | none                        | Optional `workItem.{title,type}` to override LLM brief |
| `confirm_tasks`              | `tasks-confirmed`        | no       | none                        | Optional `tasks[...]` to replace task list wholesale |
| `confirm_assignment`         | `assignment-confirmed`   | yes      | `assignee: string`          | |
| `confirm_mockup`             | `mockup-approved`        | yes      | `outcome: "approve"\|"reject"` | **NEW (FEAT-017).** Only fires for `kind="mockup"` tasks. Optional `feedback: string` on reject. |
| `confirm_plan`               | `plan-confirmed`         | yes      | none                        | Optional `plan: string` to replace LLM plan |
| `request_implementation`     | `implementation-complete`| yes      | none                        | Optional `prUrl`, `commitSha`, `summary` (FEAT-016) |
| `human_review_implementation`| `review-completed`       | yes      | `verdict: "pass"\|"fail"`  | Optional `feedback: string` on fail |

---

## New node in step stream — `advance_plan`

`generate_plan` was previously a `CompositeLLMEngineExecutor` — it produced the plan markdown and fired the T6 engine transition (planning → plan_review) in a single step. With the agent-platform integration (FEAT-016), async platform jobs can't chain a synchronous engine transition, so the node was split:

- `generate_plan` — LLM authoring step (or platform job). Writes `RunMemory.plans[taskId]`.
- `advance_plan` — fires T6 (planning → plan_review). Always follows `generate_plan`.

`confirm_plan` follows `advance_plan` as before. From a DevHub perspective, the step stream now shows `advance_plan` between `generate_plan` and `confirm_plan`. The checkpoint contract at `confirm_plan` is unchanged.

This split applies to all three agent variants (`@0.3.0`, `@0.4.0-manual`, `@0.5.0-manual`).

> **Note:** The equivalent split at the review level (`review_implementation` → `approve_review`) was done earlier in BUG-004 and has been in place since `@0.3.0`. If DevHub was already integrated with `@0.4.0-manual`, it already handles `approve_review` as a step.

---

## Agent platform integration (informational)

Starting with `@0.5.0-manual`, when `AGENT_PLATFORM_URL` is configured on the orchestrator, the `generate_tasks`, `generate_plan`, and `review_implementation` steps route to `carestechs-agent-platform` instead of the in-process LLM. This is transparent to DevHub — the checkpoint node names, signal contracts, and `nodeInputs` shapes are identical. The only observable difference is that those steps may take longer to complete (async job dispatch) and the run stays in `running` status (not `paused`) while the platform job executes.
