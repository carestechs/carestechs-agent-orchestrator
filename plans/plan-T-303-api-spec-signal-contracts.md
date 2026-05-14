# Implementation Plan: T-303 — `docs/api-spec.md` — document the four signal contracts

## Task Reference
- **Task ID:** T-303
- **Type:** Documentation
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** External callers (UI, CLI extensions, custom integrations) need a written contract for the four new signal names. The Pydantic schemas (T-297) are the source of truth in code; this doc surfaces them for humans. Required per CLAUDE.md "Documentation Maintenance Discipline."

## Overview
Add a subsection to `docs/api-spec.md` under the existing signals documentation (or wherever `POST /api/v1/runs/{runId}/signals` is described). For each of the four new signal names, document: (1) name; (2) which agent ref triggers it; (3) `taskId` requirement; (4) payload schema (camelCase, type + optional/required); (5) happy-path example body; (6) one edit example. Include a top-level note that signal names are agent-bound — delivering a v0.4.0-manual signal to a v0.3.0 run is a no-op (202 with `meta.alreadyReceived` per the existing FEAT-005 contract). Add a changelog entry at the bottom of the file.

## Implementation Steps

### Step 1: Locate the signal documentation in `docs/api-spec.md`
**File:** `docs/api-spec.md`
**Action:** Read

```bash
grep -n "signals\|implementation-complete\|POST /api/v1/runs/{runId}/signals" docs/api-spec.md
```

Identify the existing section on the `/signals` endpoint (added in FEAT-005). The four new signals are documented as siblings of `implementation-complete`.

### Step 2: Insert a new sub-section "Manual variant signals (FEAT-015)"
**File:** `docs/api-spec.md`
**Action:** Modify

Add a sub-section after the `implementation-complete` entry. Suggested structure:

```markdown
### Manual variant signals (FEAT-015)

These signals are consumed by the four `HumanExecutor` checkpoints in
`lifecycle-agent@0.4.0-manual`. Delivering them to a run started under
any other agent ref returns 202 with `meta.alreadyReceived=true` on
duplicate delivery, and is a no-op when no matching dispatch is in flight.

Endpoint: `POST /api/v1/runs/{runId}/signals`
Authentication: `Authorization: Bearer <ORCHESTRATOR_API_KEY>`
Idempotency: `(runId, name, taskId)` — duplicates return 202 + `meta.alreadyReceived=true`.

---

#### `brief-confirmed`

Resumes the run from the `confirm_brief` checkpoint after the operator
reviews the LLM-derived work-item brief.

| Field | Required | Type | Notes |
|-------|----------|------|-------|
| `name` | yes | string | Literal `"brief-confirmed"`. |
| `taskId` | no | string | Omit or pass empty string — this signal is work-item-scoped, not task-scoped. |
| `payload.workItem` | no | object | When set, overrides the corresponding `LifecycleMemory.work_item` fields. |
| `payload.workItem.title` | no | string | Replaces the LLM-derived title. |
| `payload.workItem.type` | no | string | One of `FEAT`, `BUG`, `IMP`. |

Example — approve without edits:
```json
{ "name": "brief-confirmed", "payload": {} }
```

Example — correct title and type:
```json
{
  "name": "brief-confirmed",
  "payload": { "workItem": { "title": "Corrected title", "type": "BUG" } }
}
```

---

#### `tasks-confirmed`

Resumes the run from the `confirm_tasks` checkpoint after the operator
reviews the LLM-generated task list.

| Field | Required | Type | Notes |
|-------|----------|------|-------|
| `name` | yes | string | Literal `"tasks-confirmed"`. |
| `taskId` | no | string | Omit or empty. |
| `payload.tasks` | no | array of object | When set, replaces `LifecycleMemory.tasks` wholesale. Must be non-empty if present. |
| `payload.tasks[].id` | yes (within `tasks`) | string | Task identifier — propagates as the engine task's `external_ref`. |
| `payload.tasks[].title` | yes (within `tasks`) | string | Human-readable title. |
| `payload.tasks[].summary` | no | string | Optional summary. |

`propose_tasks` then commits the edited list to the engine — one `T1.create_item`
call per task in the replacement.

Example — approve LLM list unchanged:
```json
{ "name": "tasks-confirmed", "payload": {} }
```

Example — replace with operator-curated list:
```json
{
  "name": "tasks-confirmed",
  "payload": {
    "tasks": [
      { "id": "T-001", "title": "Set up schema", "summary": "Tables + indexes" },
      { "id": "T-002", "title": "Write the route" }
    ]
  }
}
```

---

#### `plan-confirmed`

Resumes the run from a `confirm_plan` checkpoint after the operator
reviews the LLM-generated implementation plan for the current task.

| Field | Required | Type | Notes |
|-------|----------|------|-------|
| `name` | yes | string | Literal `"plan-confirmed"`. |
| `taskId` | yes | string | Must match `LifecycleMemory.current_task_id`. |
| `payload.plan` | no | string | Markdown plan content. When set, replaces `LifecycleMemory.taskPlans[taskId]`. |

Example — approve the LLM plan:
```json
{ "name": "plan-confirmed", "taskId": "T-001", "payload": {} }
```

Example — replace the plan:
```json
{
  "name": "plan-confirmed",
  "taskId": "T-001",
  "payload": { "plan": "# Updated plan\n\n1. Step one ...\n" }
}
```

---

#### `review-completed`

Resumes the run from a `human_review_implementation` checkpoint after
the operator reviews the submitted implementation.

| Field | Required | Type | Notes |
|-------|----------|------|-------|
| `name` | yes | string | Literal `"review-completed"`. |
| `taskId` | yes | string | Must match `LifecycleMemory.current_task_id`. |
| `payload.verdict` | yes | string | One of `"pass"`, `"fail"`. |
| `payload.feedback` | no | string | Optional. Recorded in `reviewHistory[].feedback`. |

A `pass` verdict routes the flow to `approve_review` (T10 → done). A
`fail` verdict routes to `correct_implementation`, which loops back to
`request_implementation` if the correction budget allows.

Example — approve:
```json
{ "name": "review-completed", "taskId": "T-001", "payload": { "verdict": "pass" } }
```

Example — reject with feedback:
```json
{
  "name": "review-completed",
  "taskId": "T-001",
  "payload": { "verdict": "fail", "feedback": "Missing edge case for empty input." }
}
```

---

### Error responses for manual-variant signals

All four signals share the standard `/signals` error envelopes:

| Status | Code | When |
|--------|------|------|
| 400 | `signal-payload-invalid` | Payload schema validation fails (missing `verdict`, empty `tasks`, malformed `type`). |
| 401 | `unauthorized` | Missing or invalid bearer token. |
| 404 | `run-not-found` | `runId` does not match any persisted run. |
| 202 | (success, with `meta.alreadyReceived=true`) | Duplicate delivery for the same `(runId, name, taskId)`. |
```

### Step 3: Update the endpoint index / cross-reference table
**File:** `docs/api-spec.md`
**Action:** Modify

If `docs/api-spec.md` has a top-level "Endpoints" table or a "Signals" index near the start, add a row pointing to the new signals subsection. If no such index exists, skip this step.

### Step 4: Add the changelog entry
**File:** `docs/api-spec.md`
**Action:** Modify

Find the changelog section at the bottom of the file. Add a new entry:

```markdown
- **YYYY-MM-DD (FEAT-015):** Documented four new signal names accepted by `POST /api/v1/runs/{runId}/signals` — `brief-confirmed`, `tasks-confirmed`, `plan-confirmed`, `review-completed`. Consumed by `lifecycle-agent@0.4.0-manual` checkpoint nodes. No endpoint-shape change; existing FEAT-005 signal contract applies.
```

Replace `YYYY-MM-DD` with the current date.

### Step 5: Verify markdown renders cleanly
**File:** N/A
**Action:** Verify

Preview the rendered markdown — either via GitHub web UI on a branch push, or with a local viewer. Confirm:
- Tables render with aligned columns.
- Code blocks are highlighted as JSON.
- Section anchors work (`#manual-variant-signals-feat-015`).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `docs/api-spec.md` | Modify | New subsection "Manual variant signals (FEAT-015)" with four signal entries + error table + changelog entry. |

## Edge Cases & Risks
- **Drift from code.** The schemas in T-297 are the source of truth. If they change post-merge, this doc lags. Mitigation: link to the source file at the top of the new subsection — `Source: src/app/modules/ai/executors/lifecycle_manual_patches.py`.
- **Stale signal note.** Delivering a manual-variant signal to a v0.3.0 run is technically a no-op, but the route still returns 202 because the `run_signals` row inserts cleanly. Document this clearly so operators don't think a 202 means "the signal advanced the run."
- **Camel vs snake in examples.** Examples use camelCase JSON (`workItem`, `taskId`) per project convention. The payload-validation layer accepts both due to `populate_by_name=True`; documenting only camelCase keeps the spec consistent with every other endpoint in this file.
- **Anchor link consistency.** GitHub auto-generates anchors from section headers; the resulting URL fragments (`#brief-confirmed`, etc.) become external references. Don't rename later without a deprecation note.

## Acceptance Verification
- [ ] AC-1 — All four signal subsections present with the field tables documented.
- [ ] AC-2 — Each signal has one happy-path example and one edit example in JSON.
- [ ] AC-3 — Error responses table documents 400 / 401 / 404 / 202-duplicate cases.
- [ ] AC-4 — Changelog entry exists with today's date and FEAT-015 reference.
- [ ] AC-5 — Markdown renders cleanly on GitHub preview.
