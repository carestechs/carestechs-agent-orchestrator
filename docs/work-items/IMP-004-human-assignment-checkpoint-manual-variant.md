# Improvement Proposal: IMP-004 — Human assignment checkpoint in `lifecycle-agent@0.4.0-manual`

> **Purpose**: Let the operator pick (or confirm) the assignee for each task before the manual lifecycle commits T5 (`assigning → planning`). Today `assign_task` fires T5 unconditionally against whatever task is current in `LifecycleMemory`, with no assignee captured — so the manual variant, which exists precisely so a human owns every commit to engine state, silently skips the one decision that has a human name attached to it. Add a fifth `HumanExecutor` checkpoint (`confirm_assignment`) that pauses for an `assignment-confirmed` signal, persists `assignee` to memory, and only then lets `assign_task` fire T5.

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | IMP-004 |
| **Name** | Human assignment checkpoint in `lifecycle-agent@0.4.0-manual` |
| **Type** | Feature gap · Manual-variant completeness |
| **Status** | Proposed |
| **Priority** | Medium |
| **Proposed By** | Operator review of v0.4.0-manual flow, 2026-05-14 — noted that the manual variant has human checkpoints at brief/tasks/plan/review but none at assignment, leaving T5 as the only LLM→engine seam without an operator gate |
| **Date Created** | 2026-05-14 |

---

## 2. Target Area

**Component / Module:** `modules/ai` — manual lifecycle agent + executors bootstrap.

**Affected Files / Directories:**
- `agents/lifecycle-agent@0.4.0-manual.yaml` (new node + transition)
- `src/app/modules/ai/executors/bootstrap.py` (new `register_lifecycle_v04_manual` binding for `confirm_assignment`; assignee memory-patch builder)
- `src/app/modules/ai/lifecycle/memory.py` (extend `LifecycleTask` with `assignee: str | None`, or top-level `assignments` sidecar — to be decided in the plan)
- `src/app/modules/ai/schemas.py` (signal payload schema for `assignment-confirmed`)
- `docs/api-spec.md` (new signal contract documented alongside `brief-confirmed` / `tasks-confirmed` / `plan-confirmed` / `review-completed`)
- `docs/data-model.md` (only if `LifecycleMemory` shape change is operator-visible)
- `tests/modules/ai/test_lifecycle_v04_manual.py` (new — end-to-end manual run asserting the pause + signal + memory + T5 firing order)

---

## 3. Current State

### How It Works Today

The manual variant `lifecycle-agent@0.4.0-manual` (FEAT-015) inserts four `HumanExecutor` checkpoints at the LLM→engine seams:

1. `confirm_brief` — before W1 `register_work_item`
2. `confirm_tasks` — before fanout `propose_tasks`
3. `confirm_plan` — between `generate_plan` (T6) and `approve_plan` (T7)
4. `human_review_implementation` — replaces the LLM reviewer between `submit_implementation` (T9) and `approve_review` (T10)

The fifth LLM→engine seam — `assign_task` (T5, `assigning → planning`) — has **no human checkpoint**. The node is bound to an `EngineExecutor` that resolves the current task via `find_current_task(memory)` and fires T5 immediately. The transition selects no assignee; `LifecycleTask` has no `assignee` field.

Today's flow segment:

```
propose_tasks → assign_task (T5) → generate_plan
```

### Problems

1. **Manual variant is incomplete by design intent.** v0.4.0-manual exists so a human owns every commit to engine state at an LLM→engine boundary. T5 is one such commit and the only one without an operator gate.
2. **No assignee is captured anywhere.** Tasks advance through `planning → impl_review → done` without ever recording who is responsible for them. This breaks the "engine is the authoritative state owner" contract for the field that downstream consumers (audit, dashboards, notifications) most want to query.
3. **Reassignment is impossible without a signal surface.** Even if an operator wanted to reassign mid-flight, there's no checkpoint at which to inject that decision.

### Evidence

- `agents/lifecycle-agent@0.4.0-manual.yaml` — `confirm_*` nodes exist at brief, tasks, plan; no `confirm_assignment`.
- `src/app/modules/ai/executors/bootstrap.py:691-704` — `assign_task` is a plain `register_engine_executor(... transition_key="task.T5" ...)` with no memory patch builder and no upstream human pause.
- `src/app/modules/ai/lifecycle/memory.py` — `LifecycleTask` has no `assignee` field; greppable.

---

## 4. Desired State

### Target Implementation

Insert one new node, `confirm_assignment`, between `propose_tasks` and `assign_task`. It's a `HumanExecutor` binding symmetric with the other four manual checkpoints:

- **Pauses** the run on a supervisor future. `Run.status` flips to `paused` via the existing IMP-002 mechanism — no new status logic.
- **Resumes** on `POST /api/v1/runs/{id}/signals` with `name=assignment-confirmed` and `payload.assignee: <string>` (required) plus optional `payload.taskId` (defaults to `LifecycleMemory.current_task_id`).
- **Persists** the assignee into `LifecycleMemory` (exact shape — field on `LifecycleTask` vs. top-level `assignments[taskId]` sidecar — decided in the implementation plan; sidecar pattern is consistent with `plans[taskId]`).
- **Transitions** to `assign_task`, which still fires T5 unchanged. (Engine-level T5 doesn't carry assignee in the workflow declaration today; capturing the assignee in memory is sufficient for v1 of this IMP. Forwarding the assignee to the engine's task-assignment aux row is a follow-on once the engine workflow declares an `assignee` field.)

Updated flow segment:

```
propose_tasks → confirm_assignment 🧑 → assign_task (T5) → generate_plan
                                   ↑
                       signal: assignment-confirmed
                       payload: { assignee: "alice", taskId?: "..." }
```

Per-task loop semantics: the `mark_task_done` → `assign_task` loop-back stays as-is, but the resolver now routes through `confirm_assignment` first — operator confirms the assignee **for each task individually**, mirroring how `confirm_plan` runs per task. This is the right granularity for the manual variant; bulk pre-assignment would re-create the "plan-all-then-implement-all" anti-pattern that BUG-013 retired.

### Benefits

1. **Manual variant becomes structurally complete** — every LLM→engine seam has a human gate.
2. **Assignee is captured** — first-class memory field, queryable per run, available to future effectors (Slack DM to assignee, etc.) and to the engine aux-row write once the workflow declares the field.
3. **Reassignment surface exists** — a future `assignment-changed` signal becomes a small additive change rather than a re-plumb.
4. **Pattern reuse** — no new executor type, no new runtime mechanism; one new `HumanExecutor` binding + one memory patch + one signal schema entry. Symmetric with the four existing manual checkpoints.

---

## 5. Trigger and Motivation

**Trigger:** Review of v0.4.0-manual on 2026-05-14 after FEAT-015 landed. Operator noticed that the manual variant's stated design intent ("operator drives every commit to engine state") has a single uncovered seam at T5, and that no field captures task ownership anywhere in `LifecycleMemory`.

**Impact if deferred:**
- The manual variant ships with a documented gap between its design intent and its actual flow.
- Any future effector keyed on "notify the assignee" has nothing to read.
- Capturing assignee retroactively (after planning or implementation) is a worse contract — the engine task is already in motion by then.

**Dependencies on this improvement:**
- A future engine-workflow update that declares `task.assignee` and forwards it on T5 (engine-side change; not in scope here).
- A future Slack/email assignment-notification effector (consumes the memory field this IMP introduces).

---

## 6. Affected Entities and Components

| Entity / Component | What Changes | Spec Reference |
|--------------------|-------------|----------------|
| `lifecycle-agent@0.4.0-manual` (agent YAML) | One new node `confirm_assignment` + edited transition `propose_tasks → confirm_assignment → assign_task`; intake schema unchanged | `agents/lifecycle-agent@0.4.0-manual.yaml` |
| `LifecycleMemory` | Adds `assignments` top-level sidecar (or `assignee` on `LifecycleTask` — TBD in plan); memory shape no longer byte-identical to v0.3.0 for this variant | `src/app/modules/ai/lifecycle/memory.py` |
| `RunSignalPayload` (api-spec) | New signal `assignment-confirmed` with required `assignee` + optional `taskId` | `docs/api-spec.md` — Signals |
| `register_lifecycle_v04_manual` (bootstrap) | One new `HumanExecutor` binding + one memory-patch builder; reuses every other v0.3.0 binding via the existing shared helper | `src/app/modules/ai/executors/bootstrap.py` |
| Trace surface | No change — pause + resume already covered by existing `executor_call` + signal-delivery traces | `docs/api-spec.md` — Trace stream |

No schema migration, no new endpoint, no new DB column. `RunSignal` already keys on `(run_id, name, task_id)` and supports arbitrary payload JSON.

---

## 7. Risk Assessment

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Operator forgets to send `assignment-confirmed`, run sits at `paused` indefinitely | High | Low | Same risk profile as every other manual checkpoint; covered by the (separate) mode-aware timeout follow-on to IMP-002. No new exposure. |
| Signal payload misses `assignee` field | Medium | Low | Pydantic v2 schema validation rejects malformed signals at the route layer; the HumanExecutor never sees a bad payload. Standard pattern. |
| Memory shape divergence from v0.3.0 hides a future drift bug | Low | Medium | Document the variant-specific memory key in CLAUDE.md's "Lifecycle agent variants are peers" pattern paragraph. Add a unit test asserting v0.3.0 memory remains free of `assignments`. |
| Loop-back from `mark_task_done` re-pauses at `confirm_assignment` for tasks already assigned | Low | Low | Intentional — operator may want to confirm or reassign per task. If batch confirmation becomes a real ask, a `payload.assignees: {taskId: assignee}` bulk variant is a small follow-on. |
| Mid-flight reassignment confusion (operator confirms assignee A, later wants B) | Medium | Low | Out of scope for this IMP — surface remains "one assignee per task at T5 time". A future `assignment-changed` signal can build on this. |
| T5 engine call fails after assignee is recorded → memory and engine diverge | Low | Medium | Existing FEAT-010 contract: `PendingAuxWrite` + reconciler. Memory-only assignee is a forward-looking record; if T5 fails the run terminates and memory is observable via the trace stream. No worse than today's state. |

### Rollback Strategy

Revert four files: the agent YAML, `bootstrap.py`, `memory.py` (drop the assignments key), `api-spec.md`. No data migration. Any in-flight run that already paused at `confirm_assignment` would need to be cancelled (`POST /api/v1/runs/{id}/cancel`) — acceptable for an opt-in variant rollback.

---

## 8. Constraints

- **Pattern fidelity.** The new node must follow the existing `HumanExecutor` + memory-patch + signal-schema pattern used by the four current checkpoints. No new executor type, no new runtime hook.
- **v0.3.0 untouched.** The shared `register_lifecycle_v03(...)` helper must not learn about assignments — the addition lives in `register_lifecycle_v04_manual` only, per the "Lifecycle agent variants are peers" pattern in CLAUDE.md.
- **Memory key must round-trip through `RunMemory` JSON storage** (Pydantic v2 model_validate / model_dump, no custom serializers).
- **No engine-workflow change.** This IMP is orchestrator-only. The engine still fires T5 as today; assignee remains in orchestrator memory until a future engine workflow declares the field.
- **Idempotency.** `(run_id, name="assignment-confirmed", task_id)` must be unique — duplicate signals return 202 with `meta.alreadyReceived=true` (existing FEAT-005 contract).

---

## 9. Success Criteria

- A live `lifecycle-agent@0.4.0-manual` run pauses at `confirm_assignment` after `propose_tasks` completes; `GET /api/v1/runs/{id}` returns `status='paused'`.
- `POST /api/v1/runs/{id}/signals` with `{"name": "assignment-confirmed", "payload": {"assignee": "alice"}}` resumes the run; `Run.status` returns to `running`; `LifecycleMemory.assignments[<currentTaskId>]` reflects `"alice"`.
- `assign_task` fires T5 immediately after resume; the engine task is in `planning`; the assignee record is observable in the trace stream.
- Multi-task runs pause at `confirm_assignment` once per task on the loop-back from `mark_task_done`.
- v0.3.0 runs are byte-unchanged — no `assignments` key appears in their memory; existing v0.3.0 tests stay green.
- New end-to-end test under `tests/modules/ai/` covers the full pause → signal → memory write → T5 fire sequence.
- `docs/api-spec.md` lists `assignment-confirmed` in the signal contract table alongside the four existing manual-variant signals.

---

## 10. Current Test Coverage

| Area | Coverage | Notes |
|------|----------|-------|
| Manual-variant human checkpoints | Integration | FEAT-015 / T-302 covers the full v0.4.0-manual lifecycle end-to-end with `brief-confirmed` / `tasks-confirmed` / `plan-confirmed` / `review-completed`. No assignment signal exists yet. |
| `HumanExecutor` dispatch + resume | Unit + integration | Covered by IMP-002 tests + FEAT-015 tests. Reused as-is. |
| `LifecycleMemory` round-trip | Unit | Existing tests cover `tasks` and `plans` sidecars; new `assignments` key needs equivalent coverage. |
| Signal payload validation | Unit | Existing pattern; need a new test for `assignment-confirmed` payload schema. |

Gap: no test today asserts assignee capture or the `propose_tasks → confirm_assignment → assign_task` ordering. This IMP introduces both.

---

## 11. Traceability

| Reference | Link |
|-----------|------|
| **Triggered By** | Operator review of v0.4.0-manual flow, 2026-05-14 |
| **Stakeholder Alignment** | Manual variant's stated intent: "operator drives every commit to engine state" — closing the one uncovered LLM→engine seam |
| **Architecture Reference** | `docs/ARCHITECTURE.md` — Lifecycle agent variants; `CLAUDE.md` — "Lifecycle agent variants are peers" pattern paragraph |
| **Related Work Items** | FEAT-015 (manual variant), IMP-002 (PAUSED status — prerequisite, already landed), FEAT-009 (executor seam), FEAT-010 (engine executor) |
| **Blocked Features** | Engine workflow update to carry `task.assignee` on T5; assignee-notification effectors (Slack/email); reassignment signal (`assignment-changed`) |
