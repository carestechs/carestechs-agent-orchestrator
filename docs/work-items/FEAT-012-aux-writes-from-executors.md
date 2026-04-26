# Feature Brief: FEAT-012 — Aux writes flow uniformly through the executor seam

> **Purpose**: Close the last inline-write gap in the FEAT-008 / FEAT-009 architecture. Today, a few aux rows still get written by signal-adapter or tool-handler code under specific paths (rejection transitions that don't call the engine, the engine-absent dev-mode fallback, and any v0.1.0-tool-side aux materialization). This FEAT consolidates those paths so that **every** aux row — `Approval`, `TaskAssignment`, `TaskPlan`, `TaskImplementation` — is materialized either (a) by the FEAT-008 reactor consuming an `EngineExecutor` outbox row, or (b) by a `LocalExecutor` registered against the rejection node that writes the aux row through a single shared helper. No aux write happens inside `service.py`, `runtime.py`, `runtime_deterministic.py`, or any tool handler.
>
> **Relationship to FEAT-008.** FEAT-008 made the reactor the sole writer of aux rows under engine-present mode and standardized the outbox. The exception was rejection transitions (T3/T8/T11), which write the `Approval` row inline because no engine call happens. This FEAT replaces that inline write with a registered rejection executor — uniform shape, uniform trace, no special case.
>
> **Relationship to FEAT-009 + FEAT-011.** FEAT-009 stood up the seam; FEAT-011 ports the lifecycle to it. While porting, every inline aux-write site surfaces; FEAT-012 is where those become executor registrations. Sequence: FEAT-010 → FEAT-011 → FEAT-012, with FEAT-012's closing tasks landable inside FEAT-011's closing PR if the ports stay clean.
>
> **Why a separate FEAT and not just FEAT-011 task overflow:** the inline rejection path predates FEAT-009; removing it touches v0.1.0 too. Keeping it scoped separately lets v0.3.0 ship without that risk and lets v0.1.0 keep working until aux-write consolidation is verified independently.
> **Template reference**: `.ai-framework/templates/feature-brief.md`

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-012 |
| **Name** | Aux writes flow uniformly through the executor seam |
| **Target Version** | v0.7.0 |
| **Status** | Not Started |
| **Priority** | Medium |
| **Requested By** | Project owner (FEAT-009 / FEAT-011 design review — inline aux-write paths are the last known FEAT-008 drift surface) |
| **Date Created** | 2026-04-26 |

---

## 2. User Story

**As an** orchestrator operator auditing how lifecycle state lands in the database, **I want** every aux row to come from either the reactor (engine-confirmed transitions) or a registered executor (rejection transitions that don't call the engine) — **so that** every aux write is observable in the trace under a uniform shape, restart-safe via the existing outbox + reconciler, and there's exactly one code path per row type, not two.

---

## 3. Goal

`grep` for direct `Approval(...)`, `TaskAssignment(...)`, `TaskPlan(...)`, `TaskImplementation(...)` constructions in `src/app/modules/ai/` returns hits only inside `executors/`, `lifecycle/reactor.py`, and the migration tests — never inside signal adapters, runtime modules, or tool handlers. Rejection transitions register a `RejectionExecutor` against the rejection node; v0.1.0 tools that wrote aux rows inline are refactored to delegate to the shared aux-write helper that the executor uses.

---

## 4. Feature Scope

### 4.1 Included

- **Audit pass** — produce `docs/design/feat-012-aux-write-inventory.md` listing every code site that constructs an aux row directly: file, line, transition key, condition. This is the first task in the FEAT and gates the rest.
- **`AuxWriteHelper`** — a single shared module under `lifecycle/aux_writes.py` exposing `write_approval(...)`, `write_task_assignment(...)`, `write_task_plan(...)`, `write_task_implementation(...)`. Each helper takes a session and the row's typed payload, applies the transition's idempotency guard, and commits inside the caller's transaction. The reactor uses these helpers; the rejection executor uses these helpers; nothing else does.
- **`RejectionExecutor`** — a `LocalExecutor` variant under `executors/rejection.py` that, on dispatch, writes the corresponding `Approval` row (or other aux row, depending on the transition) via the helper, returns the result envelope, and emits the standard `executor_call` trace entry. Registered against the rejection nodes in `lifecycle-agent@0.3.0` and (via a v0.1.0 compatibility shim — see below) against the rejection paths in v0.1.0.
- **v0.1.0 inline-write removal** — the v0.1.0 tool handlers that today write aux rows inline are refactored to call `AuxWriteHelper`. The handlers themselves stay; only the *write call* is centralized. v0.1.0 runs the same paths but through the shared helper.
- **Engine-absent dev-mode fallback consolidation** — today, when `lifecycle_engine_client` is unset, the signal adapter writes aux rows inline (preserving pre-FEAT-008 behavior). This FEAT replaces that path with a `DevModeReactor` that consumes the outbox synchronously after the signal adapter commits the outbox row — same shape as engine-present mode, just synchronous wake. Engine-absent mode keeps working operationally; it stops being a structural exception.
- **Trace coverage** — every aux write emits a `trace_kind="aux_write"` entry with `row_type`, `row_id`, `transition_key`, `correlation_id` (if any), `source` (`reactor`|`rejection_executor`|`dev_mode_reactor`). Traceable end-to-end from dispatch → outbox → aux row.
- **Idempotency consolidation** — every helper applies the same `(transition_key, entity_id, correlation_id)` idempotency check; duplicate writes return the existing row, never raise. The current scattered idempotency checks (in service.py, in tool handlers, in the reactor) collapse to one.
- **Structural guard test** — a subprocess-based test (same shape as the FEAT-009 import-quarantine test) that asserts: aux-row constructors are only imported by `executors/`, `lifecycle/reactor.py`, `lifecycle/aux_writes.py`, and explicit migrations / tests. Any new inline-write site fails the test.
- **Documentation:** `CLAUDE.md` Pattern entry "Aux writes go through `AuxWriteHelper`, called by reactor or rejection executor only"; Anti-Pattern "Don't construct aux rows inline from a signal adapter, runtime module, or tool handler"; FEAT-008 design doc updated with a "FEAT-012 supersedes the rejection-path exception" note.

### 4.2 Excluded

- **Schema changes to aux tables.** `Approval`, `TaskAssignment`, `TaskPlan`, `TaskImplementation` shapes are unchanged. This FEAT is a write-path consolidation, not a model evolution.
- **Replacing the reactor.** The reactor stays as the engine-confirmed-transition consumer. FEAT-012 only changes *who calls the helper*, not the pipeline shape.
- **Eliminating engine-absent fallback.** Dev mode keeps working; it just stops being a structural special case.
- **Generalizing the helper to non-lifecycle aux rows.** No second consumer today. Generalize when a second one shows up.
- **Auditing or reorganizing effector calls.** Effectors are an outbound seam; aux writes are an inbound persistence operation. Different surface.
- **Reworking idempotency keys.** Whatever the current key shapes are, they're preserved. This FEAT centralizes the *enforcement*, not the *definition*.

---

## 5. Acceptance Criteria

- **AC-1**: `docs/design/feat-012-aux-write-inventory.md` exists and lists every direct aux-row construction site in the repo with file:line, transition key, and the path that drives it.
- **AC-2**: After the FEAT lands, `rg "Approval\("` (and equivalents for the other three) inside `src/app/modules/ai/` returns hits only in `executors/`, `lifecycle/reactor.py`, `lifecycle/aux_writes.py`, and migrations. Verified by the structural guard test.
- **AC-3**: Every existing FEAT-008 reactor-path integration test passes unchanged. The reactor's behavior is preserved; only the *helper it calls* changes.
- **AC-4**: Every existing rejection-path integration test (T3/T8/T11) passes — under v0.1.0, under v0.3.0, and under engine-absent dev mode. The aux row that was written before is written after, with the same idempotency guarantees, just from the executor.
- **AC-5**: Engine-absent dev-mode runs (no `lifecycle_engine_client` configured) reach the same final database state as before — every aux row that the inline path would have written is written by the `DevModeReactor`. Verified by integration test that toggles the client off and runs through end-to-end.
- **AC-6**: Aux-write trace entries (`trace_kind="aux_write"`) appear for every aux row materialized — under reactor path, rejection-executor path, and dev-mode path. Forensics: any aux row in the DB joins back to exactly one trace entry.
- **AC-7**: `LIFECYCLE_MAX_CORRECTIONS` and other lifecycle limits remain enforced; no edge case reachable through the consolidated path that wasn't reachable through the inline path. Verified by parametric test re-running every aux-write fixture under both code paths during a transition window, then under the consolidated path only.
- **AC-8**: A new inline aux-write site introduced after this FEAT lands fails the structural guard test in CI. The guard is the regression bar.

---

## 6. Key Entities and Business Rules

| Entity | Role in Feature | Key Business Rules |
|--------|----------------|--------------------|
| `Approval` | Written by reactor or `RejectionExecutor`; never by signal adapter | Idempotent on `(transition_key, target_id, correlation_id)`; duplicates return existing row |
| `TaskAssignment` | Written by reactor on engine-confirmed assignment transitions | Same idempotency contract |
| `TaskPlan` | Written by reactor when planner LLM-content executor's outbox row is consumed | Plan revision tracking unchanged |
| `TaskImplementation` | Written by reactor when implementation signal arrives + correlation matches | Idempotent on `(run_id, task_id, correlation_id)` |
| `PendingAuxWrite` | Outbox row consumed by reactor / dev-mode reactor; unchanged from FEAT-008 | Single source of "intent to materialize" |
| `WebhookEvent` | Triggers reactor in engine-present mode; unchanged | Persist-first ordering preserved |

**New entities required:** None.

---

## 7. API Impact

No endpoint shape changes. Internal write-path consolidation only.

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/v1/runs/{id}/signals` | POST | Existing | Behavior preserved end-to-end; internally now enqueues outbox + relies on reactor (or dev-mode reactor) for aux materialization |
| `/hooks/lifecycle/transitions` | POST | Existing | Reactor-path unchanged |

**New endpoints required:** None.

---

## 8. UI Impact

N/A.

---

## 9. Edge Cases

- **Reactor and rejection executor race on same `(transition_key, entity_id)`.** Cannot happen by construction — rejection transitions don't call the engine, so the reactor never receives a webhook for them. But the helper's idempotency guard handles it defensively if a future workflow change introduces overlap.
- **Engine-absent mode + run cancellation mid-dispatch.** `DevModeReactor` consumes outbox synchronously, so a cancellation between outbox commit and reactor consumption is a no-op (next call drains it). Verified by test.
- **Outbox row exists but no corresponding aux-write helper match.** This is a configuration error. Surface as an explicit boot-time validation in `register_all_executors` — every transition that emits an outbox row must have a registered helper key.
- **v0.1.0 inline-write site missed by audit.** AC-1's inventory + AC-2's structural guard together close this. If something slips through, the structural guard fails CI.
- **Helper called from outside the allowlisted modules.** Structural guard catches; the helper itself does not enforce caller identity at runtime (would couple too tightly).
- **Migration window concurrent runs.** v0.1.0 runs that started before the FEAT lands and finish after must not double-write. Idempotency on aux-write keys handles this; the helper is the single chokepoint.

---

## 10. Constraints

- v0.1.0 lifecycle e2e suite must remain green. Behavioral parity is the bar.
- v0.3.0 lifecycle e2e suite (FEAT-011) must remain green.
- Must not change aux-row schemas or idempotency keys.
- Must not change the reactor's pipeline ordering (`materialize aux → consume correlation context → fire effectors → wake dispatch → fire derivations`).
- Structural guard test must run under the same subprocess pattern as the FEAT-009 import-quarantine test — order-independent, isolated.
- Single-worker constraint preserved.

---

## 11. Motivation and Priority Justification

**Motivation:** FEAT-008 said "the reactor is the sole writer" but admitted two structural exceptions: rejection transitions (no engine call → inline write) and engine-absent dev mode (no webhook → inline write). Both are *operationally fine* but they're the seams where the next architectural drift reappears. Every new aux-row use case currently has three paths to choose from; without consolidation, the FEAT-008 invariant ("reactor is the sole writer") softens into "reactor is *usually* the writer, except when…", which is exactly the shape that produced the FEAT-008 pivot.

**Impact if delayed:** Each new lifecycle feature has to navigate three write paths and pick the right one. The FEAT-008 anti-pattern ("don't write aux rows from a signal adapter") quietly becomes "don't, except in these specific cases…" — a known loophole. Audit + structural guard close this loophole permanently.

**Dependencies on this feature:** None blocking. This is the cleanup that makes the FEAT-009 architecture *complete* rather than *mostly complete*. The "remove v0.1.0 + LLM-policy runtime" follow-on FEAT depends on this transitively (cleanly removing v0.1.0 is easier when there are no inline writes left in it).

---

## 12. Traceability

| Reference | Link |
|-----------|------|
| **Persona** | Orchestrator operator |
| **Stakeholder Scope Item** | Engine is authoritative state owner (FEAT-008 invariant); orchestrator persistence is reactor-managed cache |
| **Success Metric** | Fewer code paths per state change; structural guard catches regressions in CI |
| **Related Work Items** | FEAT-006 (deterministic lifecycle flow), FEAT-008 (engine as authority), FEAT-009 (orchestrator as pure orchestrator), FEAT-010 (engine executor adapter), FEAT-011 (lifecycle deterministic port) |

---

## 13. Usage Notes for AI Task Generation

1. **Audit first.** AC-1's inventory is the gating task. No write-path refactor lands before the inventory is reviewed and pinned.
2. **Helper before executor before structural guard.** Build `AuxWriteHelper` with parity tests against current behavior; then refactor reactor + rejection paths to use it; then drop the structural guard. Doing the guard first will fail noisily in flight.
3. **Engine-absent dev-mode is part of the FEAT, not an addendum.** AC-5 is non-negotiable.
4. **v0.1.0 changes are surgical.** Only the *write call* moves into the helper; tool-handler logic stays put. v0.1.0 must be operationally indistinguishable before/after.
5. **Subprocess structural guard, same shape as FEAT-009.** Don't invent a new isolation pattern.
6. **Land FEAT-012's closing tasks alongside FEAT-011's closing PR if the audit shows ≤ 5 inline sites and the rejection-path port is clean.** Otherwise ship FEAT-012 standalone after v0.3.0 has soaked.
