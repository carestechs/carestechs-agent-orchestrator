# FEAT-008 — Effector Registry + Engine-as-Authority

> **Source:** `docs/work-items/FEAT-008-effector-registry-and-engine-authority.md`
> **Status:** Not Started
> **Target version:** v0.7.0

FEAT-008 inverts the rc2 closeout decision: engine becomes authoritative, orchestrator writes happen via a reactor that dispatches effectors on every transition. This breakdown lands the registry first, relocates FEAT-007's GitHub check into it, then migrates aux-row writes + demotes local status columns.

---

## Foundation

### T-160: Authoring ADR superseding rc2 closeout

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Description:**
Write `docs/design/feat-008-engine-as-authority.md` as the authoritative architectural decision for FEAT-008. Explicitly supersede `docs/design/feat-006-rc2-architectural-position.md` by adding a "Superseded by FEAT-008" banner at its top.

**Rationale:**
AC-2. The rc2 ADR's conclusion is load-bearing in code comments and PR review discussions; leaving it unqualified while inverting its decision is a review-time footgun.

**Acceptance Criteria:**
- [ ] New ADR states the three hard rules (engine private, orchestrator sole gateway, effectors first-class) verbatim from stakeholder-definition.
- [ ] Enumerates what changes (aux writes → reactor, status → cache, columns dropped) and what stays (signal endpoints, engine-absent fallback).
- [ ] Old ADR gets a banner + a link forward.
- [ ] Cross-linked from CLAUDE.md's architecture section if one exists.

**Files to Modify/Create:**
- `docs/design/feat-008-engine-as-authority.md` — new.
- `docs/design/feat-006-rc2-architectural-position.md` — banner.

---

### T-161: Effector protocol + registry + trace kind

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** None

**Description:**
Introduce the `Effector` protocol, an `EffectorRegistry`, and the `trace_kind="effector_call"` observability entry. No concrete effectors yet; this task lands the load-bearing seam.

**Rationale:**
AC-1, AC-11. Everything else (GitHub check relocation, assignment effector, task-generation effector) binds to this interface.

**Acceptance Criteria:**
- [ ] `src/app/modules/ai/lifecycle/effectors/__init__.py` + `registry.py` exist.
- [ ] `Effector` Protocol with `name: ClassVar[str]`, `async def fire(ctx: EffectorContext) -> EffectorResult`.
- [ ] `EffectorContext` is a frozen dataclass carrying entity type, entity id, from-state, to-state, transition, correlation id, db session, engine client, settings.
- [ ] `EffectorResult` captures `status: Literal["ok","error","skipped"]`, `duration_ms`, `error_code`, `detail`.
- [ ] `EffectorRegistry.register(transition_key, effector)` + `fire_all(ctx) -> list[EffectorResult]` — deterministic insertion-order dispatch.
- [ ] Every `fire` writes one `trace_kind="effector_call"` line with `effector_name`, `entity_id`, `duration_ms`, `status`, `error_code`.
- [ ] A failing effector never stops the registry — next effector runs; result carries the error.
- [ ] Unit tests: register/lookup/fire-order/failure-isolation/trace-emission.

**Files to Modify/Create:**
- `src/app/modules/ai/lifecycle/effectors/__init__.py`
- `src/app/modules/ai/lifecycle/effectors/registry.py`
- `src/app/modules/ai/lifecycle/effectors/context.py`
- `src/app/modules/ai/trace.py` — add the `effector_call` trace kind.
- `tests/modules/ai/lifecycle/effectors/test_registry.py`

**Technical Notes:**
Keep transport pluggable from day one — a `no-op` default effector class is fine as scaffold but the registry must not special-case it.

---

### T-162: Relocate GitHub check create/update into effectors

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-161

**Description:**
Move the inline `_post_create_check` / `_post_update_check` calls out of `lifecycle/service.py` into two effectors (`GitHubCheckCreateEffector`, `GitHubCheckUpdateEffector`) registered on `task:implementing→impl_review` and `task:impl_review→done`/`task:impl_review→rejected`.

**Rationale:**
AC-4. Proves the registry against a working feature. Behavior preservation is strict — every FEAT-007 test passes unchanged.

**Acceptance Criteria:**
- [ ] Two effectors in `src/app/modules/ai/lifecycle/effectors/github.py`.
- [ ] Registered in a composition-root module (`effectors/bootstrap.py` or similar).
- [ ] Signal adapters in `service.py` no longer call GitHub directly — only the reactor fires effectors.
- [ ] All FEAT-007 tests pass unchanged (`tests/integration/test_feat007_*`).
- [ ] Trace entries go through the registry's standard `effector_call` kind, not the ad-hoc `github_check_create*` kinds used today.

**Files to Modify/Create:**
- `src/app/modules/ai/lifecycle/effectors/github.py` — new.
- `src/app/modules/ai/lifecycle/effectors/bootstrap.py` — new, wires the registry on app startup.
- `src/app/modules/ai/lifecycle/service.py` — remove `_post_create_check`, `_post_update_check`.
- `src/app/lifespan.py` — call `register_all_effectors`.

**Technical Notes:**
Keep the `NOOP_CHECK_ID` sentinel behavior. The effector becomes a `skipped` result when the client is Noop — still traced.

---

### T-163: Request-assignment effector (log-only transport)

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-161

**Description:**
On `task: * → assigning` (T4 entry), fire a `RequestAssignmentEffector` that emits a structured log line + trace entry: "task T-001 needs an assignee — admin, see work item X." No external transport in v1.

**Rationale:**
AC-5. Proves the effector seam for a *new* capability (not just relocating old code) and closes the most obvious missing integration.

**Acceptance Criteria:**
- [ ] `RequestAssignmentEffector` in `effectors/assignment.py`.
- [ ] Registered on the `task:assigning` entry key.
- [ ] Emits log at INFO with `task_id`, `work_item_id`, `external_ref`.
- [ ] Trace entry recorded as a normal effector call.
- [ ] Unit test: fire + assert log record + trace row.

**Files to Modify/Create:**
- `src/app/modules/ai/lifecycle/effectors/assignment.py`
- `src/app/modules/ai/lifecycle/effectors/bootstrap.py` — add registration.
- `tests/modules/ai/lifecycle/effectors/test_assignment.py`

---

### T-164: Task-generation effector (inline deterministic)

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-161

**Description:**
Replace `dispatch_task_generation` stub with `GenerateTasksEffector`. v1 implementation reads the work-item title/type and produces 1-3 deterministic seed tasks (e.g., one task per top-level section of the brief, or a fixed scaffold for FEAT vs BUG vs IMP). No LLM.

**Rationale:**
AC-6. Unblocks the full flow (today you seed tasks by hand). Deterministic so it can be tested without an LLM.

**Acceptance Criteria:**
- [ ] `GenerateTasksEffector` in `effectors/task_generation.py`.
- [ ] Registered on `work_item: proposed → pending_tasks` (W1 entry).
- [ ] Creates at least 1 `Task` per fire against a fixture work item.
- [ ] Idempotent: re-firing on the same work item with existing tasks is a no-op (or produces a `skipped` result with a reason).
- [ ] Unit tests: happy path, idempotency, FEAT/BUG/IMP scaffolds.

**Files to Modify/Create:**
- `src/app/modules/ai/lifecycle/effectors/task_generation.py`
- `src/app/modules/ai/lifecycle/service.py` — remove the stub + log line.
- `tests/modules/ai/lifecycle/effectors/test_task_generation.py`

**Technical Notes:**
Reading the brief's markdown file is out of scope — use the work item's existing fields. A future effector can replace this with an LLM-backed generator without changing the registry contract.

---

### T-165: Outbox table — `pending_aux_writes` + migration

**Type:** Database
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Description:**
New table storing orphan aux-write intent: `{id, correlation_id, signal_name, payload JSONB, entity_id, enqueued_at, resolved_at NULL}`. Unique index on `correlation_id`.

**Rationale:**
AC-10 backstop. Webhook-loss recovery requires a durable record of intent captured *before* the signal returns 202.

**Acceptance Criteria:**
- [ ] `PendingAuxWrite` model in `modules/ai/models.py`.
- [ ] Alembic migration: create table + unique index.
- [ ] Migration reversible (downgrade drops table cleanly).
- [ ] `test_models.py` column assertions updated.

**Files to Modify/Create:**
- `src/app/modules/ai/models.py`
- `src/app/migrations/versions/YYYY_MM_DD_add_pending_aux_writes.py`
- `tests/modules/ai/test_models.py`

---

### T-166: `await_reactor` test helper + migrate existing FEAT-006 tests

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-161, T-165

**Description:**
Add `tests/integration/_reactor_helpers.py::await_reactor(session, predicate, timeout=5s)` — polls until `predicate(session)` returns truthy, or raises. Migrate FEAT-006 + FEAT-007 tests that currently read aux rows synchronously after a 202 to use the helper.

**Rationale:**
AC-12. The behavioral shift in T-167 breaks every synchronous assertion. Helper must land first so T-167 has something to lean on.

**Acceptance Criteria:**
- [ ] Helper supports both entity-level predicates (task status) and aux-row predicates (approval count).
- [ ] Default timeout 5s, polling interval 50ms, configurable per call.
- [ ] On timeout, raises with a diagnostic dump of current state.
- [ ] Every `assert` on aux-row state in `tests/integration/test_feat006_*.py` wrapped in `await_reactor(...)`.
- [ ] Test suite runs to completion without spurious failures.

**Files to Modify/Create:**
- `tests/integration/_reactor_helpers.py` — new.
- `tests/integration/test_feat006_e2e.py` — aux-row assertions wrapped.
- `tests/integration/test_feat007_merge_gating.py` — if affected.

**Technical Notes:**
Avoid `asyncio.sleep` polling loops at test call sites — the helper is the only place polling lives.

---

## Core migration

### T-167: Move aux-row writes (Approval, TaskAssignment, TaskPlan, TaskImplementation) to the reactor

**Type:** Backend
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-161, T-165, T-166

**Description:**
Signal adapters in `lifecycle/service.py` stop writing aux rows inline. They forward to the engine + enqueue a `PendingAuxWrite` (keyed on correlation id) + commit + return 202. The reactor, on matched webhook arrival, materializes the aux row from the outbox payload + deletes the outbox row.

**Rationale:**
AC-7. The load-bearing pivot of FEAT-008.

**Acceptance Criteria:**
- [ ] `submit_implementation_signal`, `approve_review_signal`, `reject_review_signal`, `assign_task_signal`, `assign_approve_signal`, `submit_plan_signal`, `approve_plan_signal`, `reject_plan_signal` all stop inserting aux rows directly.
- [ ] Each adapter writes a `PendingAuxWrite` with `signal_name`, `entity_id`, `payload` = whatever the old inline insert carried.
- [ ] Reactor in `lifecycle/reactor.py` gains `_materialize_aux(correlation_id)`: lookup pending row by correlation id, insert aux row, delete pending row. Idempotent on duplicate webhook arrival.
- [ ] Unit test: stub the engine to never emit a webhook → signal returns 202, no aux row, pending row exists.
- [ ] Unit test: deliver synthetic matched webhook → aux row appears, pending row deleted.
- [ ] All FEAT-006 integration tests pass with `await_reactor` wrappers.
- [ ] **Engine-absent fallback:** when `lifecycle_engine_client is None`, signal adapters fall back to inline writes (pre-FEAT-008 behavior). Explicit conditional + test.

**Files to Modify/Create:**
- `src/app/modules/ai/lifecycle/service.py` — every signal adapter.
- `src/app/modules/ai/lifecycle/reactor.py` — `_materialize_aux`.
- `tests/modules/ai/lifecycle/test_reactor_aux_materialization.py` — new.

**Technical Notes:**
This is where the outbox earns its keep. Get the idempotency + correlation-match logic right; the rest is mechanical.

---

### T-168: Drop `locked_from` + `deferred_from` columns

**Type:** Database
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-167

**Description:**
Alembic migration removes both columns. Signal adapters that set them (`lock_work_item`, `defer_task`) are updated to rely on engine transition history instead — the engine stores the prior state in its audit log and transitions back on unlock/resume.

**Rationale:**
AC-8. Redundant state; engine owns transition history.

**Acceptance Criteria:**
- [ ] Migration: drops both columns. Pre-flight check: fails loudly if any work item is currently `locked` or any task is currently `deferred` with no pathway to recover the prior state from the engine.
- [ ] `lock`/`unlock`/`defer` signal adapters no longer reference the dropped columns.
- [ ] Unlock test: locks a work item → engine records prior state → unlocks → work item returns to prior state via engine, not a local column read.
- [ ] `test_models.py` column assertions updated.

**Files to Modify/Create:**
- `src/app/migrations/versions/YYYY_MM_DD_drop_locked_from_deferred_from.py`
- `src/app/modules/ai/models.py` — remove attributes.
- `src/app/modules/ai/lifecycle/work_items.py` + `tasks.py` — adjust lock/defer logic.
- `tests/modules/ai/test_models.py`

**Technical Notes:**
Destructive migration. Pre-flight check is non-negotiable — a FEAT-008 upgrade on a db with locked work items must fail fast, not silently lose data.

---

### T-169: Demote `work_items.status` + `tasks.status` to reactor-managed cache

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-167

**Description:**
Signal adapters (and transition functions in `work_items.py` / `tasks.py`) no longer set `status`. The reactor writes it from the `item.transitioned` webhook. DTO read paths stay unchanged (the cache is populated before the signal's 202 handler responds via the read-through pattern — or via `await_reactor` in tests).

**Rationale:**
AC-9. Closes the "engine is sole writer" loop for the columns that matter most.

**Acceptance Criteria:**
- [ ] `status` assignments in signal adapters removed. Transition functions in `work_items.py`, `tasks.py` stop writing `status`.
- [ ] Reactor writes `status` in `handle_transition`, synchronized with aux-row materialization.
- [ ] Engine-absent fallback: when no engine client, transition functions still write status inline (pre-FEAT-008 behavior).
- [ ] Test: signal adapter invoked with engine stubbed out, no aux-write outbox drained → `status` column unchanged until synthetic webhook delivered.
- [ ] Existing DTO tests pass (read-through cache is transparent to callers).

**Files to Modify/Create:**
- `src/app/modules/ai/lifecycle/work_items.py`
- `src/app/modules/ai/lifecycle/tasks.py`
- `src/app/modules/ai/lifecycle/reactor.py`
- `src/app/modules/ai/lifecycle/service.py`
- `tests/modules/ai/lifecycle/test_reactor_status_cache.py`

---

### T-170: `reconcile-aux` CLI + idempotent orphan drain

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-167

**Description:**
New CLI `uv run orchestrator reconcile-aux [--since=24h]` that inspects `pending_aux_writes`, matches against engine current state per entity, and materializes any aux rows whose webhook was lost. Must be idempotent (run twice → same result).

**Rationale:**
AC-10. Without this, a webhook loss is permanent data loss. Not a "nice to have."

**Acceptance Criteria:**
- [ ] CLI command lands in `src/app/cli.py`.
- [ ] `--since` accepts `24h`, `7d`, absolute ISO-8601.
- [ ] For each pending row: queries the engine for current entity state; if the state reflects the signal already having landed, materializes the aux row + deletes the pending row.
- [ ] Idempotent — re-running produces zero changes.
- [ ] Test: inject pending rows, mock engine responses, run reconciliation, assert aux rows written + pending rows deleted.
- [ ] Dry-run flag (`--dry-run`) prints planned actions without writing.

**Files to Modify/Create:**
- `src/app/cli.py` — new command.
- `src/app/modules/ai/lifecycle/reconciliation.py` — new module, pure logic.
- `tests/modules/ai/lifecycle/test_reconciliation.py`

---

## Correctness guards

### T-171: Startup exhaustiveness validation — every transition has an effector or explicit exemption

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-161, T-162, T-163, T-164

**Description:**
On app startup (lifespan), iterate all declared transitions (from `work_item_workflow` + `task_workflow` declarations) and assert each has a registered effector OR a `@no_effector("reason")` decorator claim. Missing coverage fails startup with a clear error.

**Rationale:**
AC-3. Silent transitions are the failure mode that would undo FEAT-008's value over time.

**Acceptance Criteria:**
- [ ] `validate_effector_coverage()` runs in lifespan startup hook.
- [ ] Logs each covered transition (DEBUG); logs each `@no_effector` exemption with its reason (INFO).
- [ ] Raises at startup if a transition has neither — exception message lists the uncovered transitions.
- [ ] Test: construct a mock registry with a gap, call validate, assert raise + message.
- [ ] `no_effector` decorator exposed in effectors module; takes a required `reason: str`.

**Files to Modify/Create:**
- `src/app/modules/ai/lifecycle/effectors/validation.py`
- `src/app/lifespan.py`
- `tests/modules/ai/lifecycle/effectors/test_validation.py`

---

## Integration

### T-172: End-to-end test — reactor-authoritative FEAT-006 flow

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-167, T-169, T-171

**Description:**
Single test that drives the full 14-signal flow against a real engine (or a high-fidelity stub), asserts all aux rows land via the reactor (not the signal adapter), asserts status columns update only post-webhook, and asserts every transition fires at least one effector (or is `@no_effector`-exempt).

**Rationale:**
AC-12. The acceptance test FEAT-008 is judged against.

**Acceptance Criteria:**
- [ ] `tests/integration/test_feat008_reactor_authoritative.py` — new.
- [ ] Uses `await_reactor` for every aux-row + status assertion.
- [ ] Introspects the trace log to prove each transition fired at least one `effector_call` entry or was marked `no_effector`.
- [ ] Runs under the existing `@pytest.mark.requires_engine` gate (opt-in).
- [ ] Passes with `uv run pytest --run-requires-engine tests/integration/test_feat008_reactor_authoritative.py`.

**Files to Modify/Create:**
- `tests/integration/test_feat008_reactor_authoritative.py`

---

## Summary

| Type | Count |
|------|-------|
| Backend | 7 (T-161, T-162, T-163, T-164, T-167, T-169, T-170, T-171) |
| Database | 2 (T-165, T-168) |
| Testing | 2 (T-166, T-172) |
| Documentation | 1 (T-160) |
| **Total** | **13** |

**Complexity:** 4 × S · 7 × M · 1 × L · 0 × XL · 1 × S (doc)

**Critical path:** T-161 → T-166 → T-167 → T-169 → T-172

**Risks & open questions:**
- **T-167 is the only Large task** and the one most likely to surface subtle bugs around correlation-id matching, idempotency, and transaction boundaries. Allocate review time accordingly.
- **Engine-absent fallback** lives in two places (T-167, T-169). If its maintenance burden grows, FEAT-008+1 may want to remove it — but that decision needs evidence, not speculation.
- **FEAT-007 test assertions** may need more than the `await_reactor` wrapping if their respx mocks are call-order-sensitive; budget extra time for T-162 verification.
- **Reconciliation is hourly-run territory** (T-170) but FEAT-008 ships only the manual CLI. A cron/systemd-timer integration is a follow-on — out of scope here.
- **Task generation is deterministic in v1** (T-164). The minute someone proposes an LLM-backed generator, they should re-read the brief §"Excluded" — it's *not* in FEAT-008 deliberately.
- **Effector ordering** is insertion-order per T-161. If two effectors ever need to run in parallel or with priority, the registry needs a second look — acceptable v1 simplification.
