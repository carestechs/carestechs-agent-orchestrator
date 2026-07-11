# lifecycle-agent@0.5.0-manual — DevHub Integration Guide

**Date:** 2026-07-11  
**Previous version:** 0.4.0-manual  
**Status:** Ready for integration

---

## What changed

Tasks now carry a `kind` field. When a task's kind is `"mockup"`, the flow inserts two extra steps between assignment and planning: the orchestrator generates an HTML mockup with the LLM, then pauses at a new **confirm_mockup** checkpoint for operator approval.

All other task kinds (`"feature"`, `"bug"`, `"chore"`) skip both steps entirely — the flow proceeds directly from assignment to planning as before. No existing checkpoints changed.

**Net new surface for DevHub:** one new checkpoint (`confirm_mockup`), one new signal (`mockup-approved`), and a `kind` field on every task in the task list.

---

## Updated flow (per task)

```
confirm_assignment → assign_task → [branch]
                                      │
                     task.kind == "mockup"
                     ├── generate_mockup → confirm_mockup ← you signal here
                     │                          │
                     │                    [approve]
                     │                          ↓
                     └── (feature/bug/chore) → generate_plan → confirm_plan → …
```

On rejection at `confirm_mockup`, the orchestrator loops back to `generate_mockup` with the operator's feedback injected into the LLM prompt. The loop repeats up to `LIFECYCLE_MAX_CHECKPOINT_REJECTIONS` times (default 3); exhausting the budget terminates the run with `stop_reason=error`.

---

## Task kind field

The task list surfaced at `confirm_tasks` (`nodeInputs.tasks`) now includes a `kind` field on each task. Use it to show a visual indicator on mockup tasks before the operator approves the task list.

```json
{
  "tasks": [
    {
      "id": "T-001",
      "title": "Design login screen",
      "kind": "mockup",
      "complexity": "medium",
      "description": "...",
      "executor": "claude-code"
    },
    {
      "id": "T-002",
      "kind": "feature"
    }
  ]
}
```

**Values:** `"feature"` (default) | `"mockup"` | `"bug"` | `"chore"`

Backward-compatible: existing runs without `kind` in memory deserialize with the default `"feature"`.

---

## New checkpoint — confirm_mockup

Fires only for tasks with `kind="mockup"`, after the LLM generates the HTML mockup and before the implementation plan is authored.

### nodeInputs shape

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

**Rendering note:** `mockupHtml` is a complete, self-contained HTML document with inline CSS and no external dependencies. Render it inside a sandboxed `<iframe srcdoc="...">` — do not inject it directly into the page DOM.

### Signal to send — mockup-approved

**Approve:**
```json
{ "outcome": "approve" }
```

**Reject with feedback:**
```json
{
  "outcome": "reject",
  "feedback": "The layout needs a larger CTA button and a logo slot at the top."
}
```

---

## Orchestrator signal contract

| DevHub checkpointKey      | Orchestrator signal name  | Status    |
|---------------------------|---------------------------|-----------|
| `brief-confirmed`         | `brief-confirmed`         | unchanged |
| `tasks-confirmed`         | `tasks-confirmed`         | unchanged |
| `assignment-confirmed`    | `assignment-confirmed`    | unchanged |
| `confirm_mockup`          | `mockup-approved`         | **NEW**   |
| `plan-confirmed`          | `plan-confirmed`          | unchanged |
| `implementation-complete` | `implementation-complete` | unchanged |
| `review-completed`        | `review-completed`        | unchanged |

---

## Unchanged checkpoints

All five existing `@0.4.0-manual` checkpoints are present in `@0.5.0-manual` with identical `nodeInputs` shapes and signal contracts. The only change to existing data is that every task object now includes a `kind` field (default `"feature"`, backward-compatible).

---

## DevHub implementation checklist

- [ ] Register `confirm_mockup` checkpoint contract with `allowedOutcomes: ["approve", "reject"]`.
- [ ] Map `checkpointKey="confirm_mockup"` → orchestrator signal `name="mockup-approved"` in `OrchestratorExecutorClient`.
- [ ] At `confirm_mockup`, render `nodeInputs.mockupHtml` in a sandboxed `<iframe srcdoc>` alongside `nodeInputs.mockupDescription` as a caption.
- [ ] Surface a `feedback` text field when the operator selects "reject" — send it in the signal payload.
- [ ] Show a visual badge or indicator on tasks with `kind="mockup"` in the task list view (at `confirm_tasks` and in task detail views).
- [ ] Confirm the work item stream correctly advances `currentCheckpointKey` to `confirm_mockup` for mockup tasks and skips it for all other kinds.
- [ ] Test the rejection loop: reject once with feedback → run returns to `generate_mockup` → new mockup arrives at `confirm_mockup` with updated HTML.
