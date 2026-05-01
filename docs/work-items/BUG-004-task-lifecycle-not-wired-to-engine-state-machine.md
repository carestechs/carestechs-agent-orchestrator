# Bug Report: BUG-004 — Task lifecycle nodes fire wrong-entity, wrong-status engine transitions

> **Purpose**: Capture the architectural gap surfaced by the live `lifecycle-agent@0.3.0` run after BUG-003 unblocked W1 creation.
> **Template reference**: `.ai-framework/templates/bug-report.md`

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | BUG-004 |
| **Summary** | The task-lifecycle nodes (`generate_tasks`, `assign_task`, `generate_plan`, `request_implementation`, `review_implementation`, `close_work_item`) were wired to fire `task.T*` transitions against the work item's `engineItemId`, with `to_status` values that don't exist on the work-item state machine. v0.3.0 has no engine task entities (no T1 fanout), no per-task `engineItemId` plumbing, and no work-item W2/W4 transitions. The engine refuses every dispatch past the first one. |
| **Severity** | High (blocks v0.3.0 production runs immediately after `register_work_item` succeeds) |
| **Status** | Open |
| **Reported By** | Live `lifecycle-agent@0.3.0` run via `real-run.sh` against Anthropic + flow-engine, after BUG-003 PR #53 merged |
| **Date Reported** | 2026-05-01 |
| **Date First Observed** | 2026-05-01 |
| **Related** | FEAT-011 (introduced the v0.3.0 wiring); BUG-003 (unblocked the prior layer); FEAT-005 / FEAT-006 / FEAT-008 (the v0.1.0 task lifecycle this should mirror) |

### Severity Justification

Identical bite to BUG-003 — the run still cannot complete end-to-end. Different shape: BUG-003 was a missing executor; BUG-004 is a fundamentally undersized model. The PR-3 simplification note ("T1xN fanout collapsed into a single self-approve transition") understated the gap: the collapsed transition addresses the wrong entity with a non-existent status, and every downstream node has the same defect. v0.1.0 lifecycle-agent is unaffected.

---

## 2. Steps to Reproduce

**Preconditions:** flow engine running; orchestrator configured; `LLM_PROVIDER=anthropic`; PR #51 (LLM-tool wiring) and PR #53 (BUG-003 split) merged.

1. `uv run orchestrator run lifecycle-agent@0.3.0 --intake workItemPath=docs/work-items/FEAT-099.md --follow`.
2. `load_work_item` → LLM brief, persists into `LifecycleMemory.work_item`.
3. `register_work_item` → engine `create_item` (W1) ok, work item created in `open` state, `Run.intake.engineItemId` set.
4. `generate_tasks` → composite calls `transition_item(work_item_id, to_status="approved")`.
5. **Observe:**
   `engine_error: transition_item: 422  "Transition from 'open' to 'approved' is not allowed. Valid transitions: in_progress"`.

**Reproducibility:** Always — deterministic given the v0.3.0 wiring.

---

## 3. Root Cause

### State machines (declared in `src/app/modules/ai/lifecycle/declarations.py`)

**Work item:** `open → in_progress → ready → closed` (with `locked` side branch).
- W1 creates at `open`. Only valid edge from `open` is `in_progress` ("approve-first-task").

**Task:** `proposed → approved → assigning → planning → plan_review → implementing → impl_review → done`.
- Initial state `proposed`. Each task is its own engine entity, created via `create_item(task_workflow_id, …)`.

### v0.1.0 (lifecycle-agent@0.1.0) reference behaviour

`src/app/modules/ai/lifecycle/tasks.py` and `work_items.py`:
- `propose_task` (T1): per-task `create_item(task_workflow)` → engine task entity at `proposed`; local `Task` row mirrors.
- `approve_task` (T2+T4): `proposed → approved → assigning` per task, `Approval(stage=proposed)` row inline.
- `assign_task_signal` → T5: `assigning → planning`.
- `submit_plan` → T6: `planning → plan_review`. `approve_plan` → T7: `plan_review → implementing`.
- After implementation signal → T9: `implementing → impl_review`.
- `approve_review` → T10: `impl_review → done`.
- `reject_*` → Approval-only, no engine call (FEAT-008 contract).
- `maybe_advance_to_in_progress` → W2 fires when first task transitions to `approved`.
- `maybe_advance_to_ready` → W4 fires when all tasks are terminal (`done`/`deferred`).
- `close_work_item` → W6: `ready → closed`.

### v0.3.0 wiring (current state, in `register_lifecycle_v03`)

```
generate_tasks         CompositeLLMEngineExecutor  transition_key="task.T2_T4"  to_status="approved"
assign_task            EngineExecutor              transition_key="task.T5"     to_status="assigned"
generate_plan          CompositeLLMEngineExecutor  transition_key="task.T6_T7"  to_status="implementation_pending"
request_implementation HumanExecutor               (no engine call)
review_implementation  CompositeLLMEngineExecutor  transition_key="task.T10"    to_status="ready_for_close"
correct_implementation LocalExecutor               (Approval inline; no engine call) ✓
close_work_item        EngineExecutor              transition_key="work_item.W6" to_status="closed"
```

Defects, per node:

| Node | Defect |
|------|--------|
| `generate_tasks` | (1) `engineItemId` in intake is the **work item's** id (BUG-003 wrote it there). (2) `"approved"` is a task status, not a work-item status. (3) No T1 fanout — engine has zero task entities. (4) Missing W2 (`open → in_progress`) entirely. |
| `assign_task` | Calls `transition_item(work_item_id, "assigned")`. `"assigned"` is not a status on either workflow. Real T5 is `assigning → planning` on a *task* entity that doesn't exist. |
| `generate_plan` | Calls `transition_item(work_item_id, "implementation_pending")`. `"implementation_pending"` is not a status on either workflow. Real T6+T7 is `planning → plan_review → implementing` on a task entity. |
| `request_implementation` | OK as a pause, but doesn't fire the post-resume T9 (`implementing → impl_review`). Listed as a PR-3 simplification. |
| `review_implementation` | Calls `transition_item(work_item_id, "ready_for_close")`. `"ready_for_close"` does not exist; real T10 is `impl_review → done` on a task. |
| `close_work_item` | Calls `transition_item(work_item_id, "closed")`. Work item is at `in_progress` (or never advanced past `open`). `closed` requires `ready` first; real W6 is `ready → closed` and depends on prior W4 (`in_progress → ready`). |

The pattern: every task-related node was authored as if `engineItemId` were a generic handle and `to_status` were the symbolic next-stage name in the LLM-policy v0.1.0 mental model. The engine state machine never validates this in tests because the test mocks return 200 unconditionally; the live engine validates and refuses.

---

## 4. Why this wasn't caught earlier

- v0.3.0 e2e tests stub `POST /api/items/<id>/transitions` to always return 200. State-machine validation is server-side; the mock has no equivalent. Live engine is the first place the contract is enforced.
- BUG-003 unblocked W1 creation, which moved the failure surface from "first node" to "second engine touchpoint." Before BUG-003 the run never reached `generate_tasks`, so the underlying gap was masked.
- The PR-3 simplifications note acknowledged "T1xN fanout collapsed" but framed it as a deferral, not as a defect. The collapse not only dropped fanout but also pointed every downstream `engineItemId` reference at the wrong entity.

---

## 5. Proposed Fix

### Scope

Properly wire the task lifecycle so v0.3.0 matches v0.1.0's engine state-machine fidelity:

1. **T1 fanout via `EngineCreateExecutor`**, one engine task entity per LLM-produced task.
2. **Per-task `engineItemId` plumbing** in `LifecycleMemory.tasks[*]`, with downstream nodes addressing the *current* task's engine id (resolved from `current_task_id`) instead of `Run.intake.engineItemId`.
3. **Real task transitions** wired per node:
   - `generate_tasks`: T1×N + T2 + T4 (proposed → approved → assigning) for every task.
   - `assign_task`: T5 (assigning → planning) for the current task.
   - `generate_plan`: T6 + T7 (planning → plan_review → implementing) for the current task.
   - `request_implementation`: human pause, then post-resume T9 (implementing → impl_review).
   - `review_implementation`: T10 (impl_review → done) on `verdict=pass`.
   - `correct_implementation`: unchanged (Approval inline; FEAT-008 contract preserved).
4. **Work-item transitions** wired explicitly:
   - W2 (open → in_progress) — fired once after the first task hits `approved`. Practical seam: register a small post-`generate_tasks` engine-only node, **or** chain W2 inline as part of the `generate_tasks` composite.
   - W4 (in_progress → ready) — fired once all tasks reach a terminal state. Practical seam: post-`review_implementation` (last task only), or as the first hop inside `close_work_item`.
   - W6 (ready → closed) — `close_work_item` becomes a two-hop sequence: W4 then W6.

### Mechanism — new and reused executors

- **`EngineCreateExecutor`** (BUG-003) reused for T1 fanout against the task workflow.
- **New executor flavour or extension** to address "current task engine id" — choices:
  - **Option B-1:** add a `target_id_resolver: Callable[[DispatchContext, RunMemory], UUID]` parameter to `EngineExecutor` / `CompositeLLMEngineExecutor` so each binding declares how to resolve its target. Default still reads `engineItemId` from intake.
  - **Option B-2:** thread the current task's engine id into `Run.intake.engineItemId` at every iteration where `current_task_id` changes. Fewer code changes, but couples the runtime to a v0.3.0-specific concern.
  - Recommendation: **B-1**, because it keeps the executor seam generic and makes the per-task threading explicit at registration time.
- **Multi-step executor for fanout/multi-hop**. The current `CompositeLLMEngineExecutor` is single-LLM-call + single-engine-transition. We need either:
  - A `SequenceEngineExecutor` that chains N transitions in one dispatch (used by `close_work_item` for W4→W6, and by `generate_tasks` for T2→T4 per task), OR
  - Multiple registrations split into separate nodes.
  - Recommendation: introduce `SequenceEngineExecutor`; keep the YAML node count bounded.

### Suggested staging — two PRs

**PR-1 (task entities + per-task threading):**
- Add `target_id_resolver` to `EngineExecutor` and `CompositeLLMEngineExecutor`.
- Build `SequenceEngineExecutor`.
- Rewire `generate_tasks`: LLM result → T1 fanout (one `EngineCreateExecutor` invocation per task) + T2+T4 sequence per task; persist each task's `engineItemId` into `LifecycleMemory.tasks[i].engineItemId`.
- Rewire `assign_task`, `generate_plan`, `review_implementation` to use a `current_task_engine_id` resolver reading from `LifecycleMemory.tasks[current_task_id].engineItemId`.
- Update `LifecycleMemory.LifecycleTask` schema: add `engine_item_id: UUID | None`.

**PR-2 (work-item transitions + close path):**
- Wire W2 (`open → in_progress`) — likely as a small post-`generate_tasks` engine-only node `start_work_item` (or chained inside).
- Wire W4 (`in_progress → ready`) — practical seam: first hop inside `close_work_item`.
- Make `close_work_item` a `SequenceEngineExecutor` chaining W4 + W6.
- Wire post-resume T9 (`request_implementation` → `submit_implementation`) so the human pause hands off cleanly to `review_implementation`.

### Verification

- New unit tests for `target_id_resolver` and `SequenceEngineExecutor`.
- Updated AC-1 e2e: trace shows engine calls in real order — W1 create → T1×N create → T2 + T4 per task → W2 (after first approval) → T5 → T6 + T7 → (resume) → T9 → T10 → W4 → W6.
- v0.1.0 regression bar (AC-7) green.
- Live re-run advances all the way to `close_work_item`.

### Out of Scope

- Multi-tenant cache work (BUG-002 already handles).
- FEAT-012 (folding rejection-path Approvals through the outbox).
- Remote / `assign_task` actually delegating to a real executor outside the orchestrator (still a single `EngineExecutor` here; sub-executor delegation is a separate FEAT).

---

## 6. Why Option A (one-layer fix) was rejected

A minimal fix that just patches `generate_tasks` to fire W2 (`open → in_progress`) would unblock the work item but leave `assign_task`, `generate_plan`, `review_implementation`, and `close_work_item` all configured to fire transitions against the wrong entity with non-existent statuses. Each one would surface in turn as a fresh bug after the prior one was patched. Estimated 4-5 layer-peeling cycles to reach `close_work_item`. The orchestrator's engine view of the world during that intermediate period would be "one work item walking open→in_progress→…→closed with no tasks underneath" — poor audit trail and a ratchet against ever doing this properly.

Option B addresses the whole class in one concerted effort.

---

## Changelog

- 2026-05-01 — Filed after BUG-003 unblocked the prior layer; recommended Option B (full task-lifecycle wiring) over Option A (one-layer minimal patch). Two-PR staging proposed.
