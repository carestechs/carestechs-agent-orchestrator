# Improvement Proposal: IMP-006 — Rejection support for human checkpoints

> **Purpose**: Human checkpoints in `lifecycle-agent@0.4.0-manual` are approve-only gates. An operator who disagrees with an LLM-generated artefact (brief summary, task list, plan) has no way to reject it — the only escape is cancelling the entire run. This improvement adds rejection edges to the flow graph and extends the payload schemas so operators can reject with feedback and route the flow back to the producing node.

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | IMP-006 |
| **Name** | Rejection support for human checkpoints |
| **Type** | Developer Experience |
| **Status** | Completed |
| **Priority** | High |
| **Proposed By** | Operator observation during DevHub integration (2026-05-24) |
| **Date Created** | 2026-05-24 |

---

## 2. Target Area

**Component / Module:** Agent flow definitions, payload schemas, deterministic runtime

**Affected Files / Directories:**
- `agents/lifecycle-agent@0.4.0-manual.yaml` — flow graph (rejection edges)
- `src/app/modules/ai/executors/lifecycle_manual_patches.py` — payload schemas + patch builders
- `src/app/modules/ai/flow_predicates.py` — new predicates for rejection branching
- `src/app/modules/ai/executors/bootstrap.py` — checkpoint registrations

---

## 3. Current State

### How It Works Today

Human checkpoints (`confirm_brief`, `confirm_tasks`, `confirm_assignment`, `confirm_plan`, `human_review_implementation`) are wired as straight-through gates. The operator sends a signal (e.g. `brief-confirmed`) and the flow always advances to the next node. The payload schemas use `extra="forbid"` so any field not explicitly declared (like a `feedback` field for rejection) causes a `ValidationError`.

### Problems

1. **No rejection path**: The flow graph has no edge from any checkpoint back to its producing node (e.g. `confirm_brief → load_work_item`). Even if the signal delivered successfully, the flow resolver has nowhere to route a rejection.
2. **Payload validation rejects unknown fields**: `BriefConfirmedPayload`, `TasksConfirmedPayload`, `PlanConfirmedPayload` all use `extra="forbid"`. A rejection payload like `{"verdict": "reject", "feedback": "..."}` would fail validation before reaching any handler.
3. **No interim escape**: The only option for an operator who disagrees is to cancel the entire run and start over — losing all progress from prior checkpoints.

### Evidence

- DevHub wired a Reject button that produces a 400/500 error because the payload schema rejects the `feedback` field.
- Operator must cancel and re-run from scratch to correct a single artefact, even if the other 4 checkpoints were already approved.

---

## 4. Desired State

### Target Implementation

Each human checkpoint accepts a `verdict` field (`"approve"` or `"reject"`). On approve, the flow advances as today (with optional corrections). On reject, the flow loops back to the producing node so the LLM regenerates the artefact. An optional `feedback` field on rejection is persisted in memory so the producing node's prompt can incorporate it on the retry.

### Benefits

1. **Operator can reject without losing progress**: A rejected brief loops back to `load_work_item`; prior checkpoint state is preserved.
2. **Feedback-driven retry**: The LLM sees the operator's rejection feedback on the next attempt, producing a better artefact.
3. **DevHub reject button works**: The payload schema accepts `{verdict: "reject", feedback: "..."}` cleanly.

---

## 5. Trigger and Motivation

**Trigger:** DevHub integration surfaced that the Reject button is non-functional — the orchestrator rejects the payload and the run gets stuck.

**Impact if deferred:** Operators must cancel entire runs to correct a single artefact. In a multi-checkpoint flow with engine state (work items, tasks created in the flow engine), cancellation leaves orphaned engine entities that require manual cleanup.

**Dependencies on this improvement:** None blocking, but DevHub's checkpoint UI is incomplete without this.

---

## 6. Affected Entities and Components

| Entity / Component | What Changes | Spec Reference |
|--------------------|-------------|----------------|
| `lifecycle-agent@0.4.0-manual.yaml` | Add `branch:` blocks on checkpoint nodes with rejection edges | `agents/` |
| `BriefConfirmedPayload` et al. | Add `verdict` and `feedback` fields | `lifecycle_manual_patches.py` |
| `flow_predicates.py` | New `checkpoint_approved` predicate (reads verdict from last dispatch result) | `src/app/modules/ai/` |
| `bootstrap.py` | Patch builders handle rejection (persist feedback to memory) | `src/app/modules/ai/executors/` |

---

## 7. Risk Assessment

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Rejection loop with no bound — operator keeps rejecting, LLM keeps regenerating | Medium | Low | Add a `LIFECYCLE_MAX_CHECKPOINT_REJECTIONS` bound (default 3), matching the existing correction-attempt pattern |
| Engine state already committed before rejection — e.g. `confirm_tasks` rejects after `propose_tasks` created engine entities | Low | Medium | Only checkpoints that gate *before* engine writes can loop back. `confirm_brief` (before W1), `confirm_tasks` (before T1), `confirm_plan` (before T7) are safe. `confirm_assignment` and `human_review_implementation` need careful analysis — they may not be rejectable without engine rollback |
| Prompt doesn't incorporate feedback effectively | Medium | Low | Include rejection feedback as a structured section in the system prompt; test with representative rejection reasons |

### Rollback Strategy

Rejection edges are additive to the flow graph. Removing them reverts to the current approve-only behavior. No migration needed.

---

## 8. Constraints

- The `lifecycle-agent@0.3.0` (autonomous) flow MUST NOT be affected — rejection is a manual-variant feature only.
- Engine state that has already been committed (e.g. work item created via W1, tasks created via T1) cannot be rolled back. Rejection edges must only loop back to nodes that precede the engine commit.
- The `extra="forbid"` strictness on payload schemas is valuable for catching typos — don't relax it globally. Extend the schemas with explicit fields instead.
- Rejection feedback MUST be persisted in `RunMemory` so the producing node's prompt can read it. Use a top-level `rejections` sidecar (like `assignments`, `plans`).

---

## 9. Success Criteria

- Operator can reject at `confirm_brief`, `confirm_tasks`, and `confirm_plan` via `{verdict: "reject", feedback: "..."}` and the flow loops back to the producing node.
- The producing node's LLM prompt includes the rejection feedback on retry.
- Approve with no `verdict` field (current behavior) still works — backward compatible.
- A rejection budget prevents infinite loops.
- DevHub's Reject button produces a clean 202 response and the run re-enters the producing node.

---

## 10. Current Test Coverage

| Area | Coverage | Notes |
|------|----------|-------|
| `lifecycle_manual_patches.py` | Good | Unit tests for all existing patch builders |
| `test_lifecycle_v04_manual.py` | Good | E2e test covers happy-path approve flow |
| `flow_predicates.py` | Good | Unit tests for all existing predicates |
| Rejection flow | None | No test coverage — feature doesn't exist yet |

---

## 11. Traceability

| Reference | Link |
|-----------|------|
| **Triggered By** | DevHub integration — Reject button non-functional (2026-05-24) |
| **Stakeholder Alignment** | Philosophy #3: "Operator always has the last word" |
| **Architecture Reference** | AD-3 (Agent is Flow + Policy + Memory), FEAT-015 (manual variant) |
| **Related Work Items** | FEAT-015 (manual variant), IMP-004 (assignment checkpoint), BUG-014/BUG-015 (same discovery session) |
| **Blocked Features** | None directly, but DevHub checkpoint UX is incomplete without this |

---

## 12. Usage Notes for AI Task Generation

- Phase the work by checkpoint: start with `confirm_brief` (simplest — no engine state precedes it), then `confirm_tasks` (before `propose_tasks`), then `confirm_plan` (before `approve_plan` / T7).
- `confirm_assignment` and `human_review_implementation` may not be rejectable without engine-side rollback — investigate before adding rejection edges for these two.
- The `verdict` field should default to `"approve"` for backward compatibility — existing signals without `verdict` must keep working.
- The rejection feedback sidecar in memory should follow the `assignments` / `plans` pattern: top-level `rejections[checkpoint_name]` with `{feedback, attempt}`.
- The flow graph branch syntax already supports this via `branch: {rule: checkpoint_approved, "true": next_node, "false": producing_node}`.
