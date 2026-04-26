# FEAT-010 — Engine executor adapter

> **Source:** `docs/work-items/FEAT-010-engine-executor-adapter.md`
> **Status:** Not Started
> **Target version:** v0.5.0

FEAT-010 adds the missing seam between FEAT-009's deterministic dispatch model and FEAT-006/008's flow-engine integration. A new `EngineExecutor` joins `LocalExecutor`, `RemoteExecutor`, and `HumanExecutor` as the fourth sibling on the executor seam — its dispatch maps to a flow-engine workflow transition, encodes a correlation id, enqueues a `PendingAuxWrite` row, and is woken by an extension to the existing `lifecycle/reactor.py` pipeline. No new persistence surface, no new webhook endpoint, no LLM in the runtime loop.

The numbering picks up at **T-230** (FEAT-009 used T-210..T-229; T-229 is reserved so FEAT-009's docs sweep can grow without collision).

---

## Foundation

### T-230: Design doc — engine executor adapter (sequel to feat-009-pure-orchestrator)

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Description:**
Write `docs/design/feat-010-engine-executor.md` as the architectural decision behind FEAT-010. State the gap FEAT-009 left (no executor advances engine state), the chosen shape (an `EngineExecutor` that produces a `PendingAuxWrite` row + calls `FlowEngineLifecycleClient.transition` in one transaction, then is woken by the FEAT-008 reactor), and the canonical reactor pipeline ordering this FEAT extends: `materialize aux → consume correlation context → fire effectors → wake dispatch → fire derivations`. Cross-link from `CLAUDE.md` Patterns ("Engine-bound nodes register an `EngineExecutor`, never call the engine inline").

**Rationale:**
AC-1, AC-5. Reviewers and future contributors must be able to point at one document for "why is there a fourth executor mode in everything but mode literal" and "where does the dispatch wake go relative to effector dispatch and W2/W5 derivations." Without the doc, the next pivot regrows the inline-engine-call shape FEAT-008 inverted.

**Acceptance Criteria:**
- [ ] New design doc explicitly names the principle: engine-bound producers register an executor; the runtime is unchanged; the reactor is the single point that wakes any dispatch the engine confirms.
- [ ] Lists what changes (new `EngineExecutor`, reactor gains a wake-dispatch step, new reconciler) and what stays (FEAT-008 outbox + reactor + correlation contract; FEAT-009 registry + coverage; v0.1.0 LLM-policy path).
- [ ] Documents the reactor pipeline order at the wake point and the rationale (effectors run before wake so any state derived by effectors is observable by the resumed runtime; derivations run after wake so the runtime advances on the originating transition's outcome, not a derived one).
- [ ] Cross-linked from `CLAUDE.md` architecture section and from `docs/design/feat-009-pure-orchestrator.md`.
- [ ] Mentions the import-quarantine extension landing in T-237 — `executors/engine.py` takes `FlowEngineLifecycleClient` only via constructor injection.

**Files to Modify/Create:**
- `docs/design/feat-010-engine-executor.md` — new.
- `CLAUDE.md` — Patterns entry + cross-link.
- `docs/design/feat-009-pure-orchestrator.md` — forward-link banner.

---

## Backend — engine seam

### T-231: `EngineExecutor` — dispatch + outbox + transition in one transaction

**Type:** Backend
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-230

**Description:**
Introduce `src/app/modules/ai/executors/engine.py` — `EngineExecutor` implementing the FEAT-009 `Executor` Protocol. Constructor parameters: `ref: str`, `transition_key: str` (e.g. `"work_item.W4"` or `"task.T6"`), `lifecycle_client: FlowEngineLifecycleClient`, `session_factory: async_sessionmaker[AsyncSession]`. `mode: ClassVar[ExecutorMode]` = `"engine"` (extending the literal in `executors/base.py`).

Dispatch behavior: open a session via `session_factory`; generate a fresh correlation id; in **one transaction** insert a `PendingAuxWrite` row keyed on the correlation id and call `lifecycle_client.transition(...)` with `triggered_by` encoding that correlation id; commit; return a `dispatched` envelope. The supervisor's per-dispatch future is later resolved by the reactor (T-233).

`FlowEngineLifecycleClient` is **constructor-injected** — never imported at module scope. This preserves the FEAT-009 import-quarantine discipline (verified by T-237).

**Rationale:**
AC-1. The `EngineExecutor` is the dispatch-shaped entry into FEAT-008's outbox + reactor pipeline. One executor instance per `(agent_ref, node_name)` binding mirrors how `RemoteExecutor` is one instance per remote URL.

**Acceptance Criteria:**
- [ ] `executors/base.py` `ExecutorMode` literal extended to `Literal["local", "remote", "human", "engine"]`. `DispatchEnvelope.mode` accepts `"engine"`.
- [ ] `executors/engine.py` exists; class `EngineExecutor` with the constructor signature above; `mode: ClassVar[ExecutorMode] = "engine"`.
- [ ] Dispatch opens its own session (not the loop's) and commits the outbox row + the engine call atomically — a 4xx/5xx from the engine rolls back the outbox insert.
- [ ] Engine 4xx → returns `failed` envelope with `outcome="error"` and `detail` carrying the engine status + body excerpt; no retry (4xx is a contract violation per the brief).
- [ ] Engine 5xx / network → bounded retry inherited from `FlowEngineLifecycleClient` retry policy. After exhaustion, returns `failed`. No outbox row is committed.
- [ ] Engine 2xx → returns `dispatched` envelope with `result` containing `transition_key`, `correlation_id`, and `engine_run_id` (if surfaced by the client).
- [ ] No module-scope import of `FlowEngineLifecycleClient`; only a `TYPE_CHECKING` import for the type alias.
- [ ] Unit tests via `respx`-stubbed engine: success path, 4xx path, 5xx-then-success retry path, 5xx-exhaustion path, outbox-commit-failure rollback.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/base.py` — extend `ExecutorMode` literal.
- `src/app/modules/ai/executors/engine.py` — new.
- `src/app/modules/ai/schemas.py` — accept `"engine"` in `DispatchEnvelope.mode` if currently constrained.
- `tests/modules/ai/executors/test_engine_executor.py` — new.

**Technical Notes:**
The transactional shape mirrors the existing FEAT-008 signal-handler pattern (`modules/ai/lifecycle/service.py`): outbox enqueue + engine call inside one `AsyncSession.begin()` block; commit only if both succeed. Reuse `extract_correlation_id` / `encode_triggered_by` helpers from `lifecycle/engine_client.py` rather than reinventing the encoding.

---

### T-232: `register_engine_executor` bootstrap helper

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-231

**Description:**
Add `register_engine_executor(registry, agent_ref, node_name, transition_key, *, lifecycle_client, session_factory)` in `executors/bootstrap.py`. Constructs an `EngineExecutor` and registers it under `(agent_ref, node_name)`. Operator-facing: FEAT-011's deterministic lifecycle agent will use this to wire each engine-bound node in one line.

`register_all_executors` is **not** modified to auto-wire engine bindings for any existing agent — `lifecycle-agent@0.1.0` continues to drive the engine via the LLM-policy path through `FlowEngineLifecycleClient` directly, not through the executor seam. v0.2.0 has no engine-bound nodes. The first real consumer is FEAT-011.

**Rationale:**
AC-1. Mirrors how `LocalExecutor` is registered today — bootstrap is the single source of truth for executor wiring; agents declare nodes, bootstrap binds executors.

**Acceptance Criteria:**
- [ ] Helper signature documented; takes the registry, the binding key, the transition key, and the two collaborators (`lifecycle_client`, `session_factory`).
- [ ] Idempotent: duplicate registration raises `ExecutorRegistryError` per the existing registry contract.
- [ ] No-op if `lifecycle_client` is `None` (engine-absent dev mode) — raises a clear `RuntimeError` naming the binding so misconfiguration surfaces at boot, not at first dispatch.
- [ ] Unit test exercises the helper end-to-end against a stub registry + stub client.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/bootstrap.py` — `register_engine_executor` helper.
- `tests/modules/ai/executors/test_bootstrap_engine.py` — new.

---

## Backend — reactor wake

### T-233: Reactor extension — wake dispatch after effector dispatch

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-231

**Description:**
Extend `src/app/modules/ai/lifecycle/reactor.py` so that when the reactor processes a webhook whose correlation id matches a `Dispatch` row in `dispatched` state with `mode="engine"`, it calls `supervisor.deliver_dispatch(dispatch_id, envelope)` after effector dispatch and **before** W2/W5 derivations.

Final pipeline order (this is the contract from the brief — verified by T-235): `materialize aux → consume correlation context → fire effectors → wake dispatch → fire derivations`.

The wake step looks up the dispatch by `correlation_id`, builds a `completed` envelope from the engine's `to_status` + transition outcome, calls `mark_completed` on the row, calls `supervisor.deliver_dispatch`. Idempotent — second arrival finds the dispatch already terminal and no-ops.

**Rationale:**
AC-1, AC-5. Today the reactor wakes signal listeners only (via `_consume_correlation`); engine dispatches have no wake path because the runtime loop in v0.1.0 awaits the engine call's HTTP response, not a webhook. FEAT-009's deterministic loop never blocks on the engine HTTP call — it awaits the supervisor future — so the wake hook is the missing leg.

**Acceptance Criteria:**
- [ ] New private function in `reactor.py`: `_wake_dispatch(db, correlation_id, event, supervisor) -> None`. No-ops if no matching dispatch row, or the row is already terminal.
- [ ] Pipeline order in `handle_transition` is the canonical order from the brief: `_materialize_aux → _consume_correlation → _update_status_cache → _dispatch_effectors → _wake_dispatch → derivations`. (`_update_status_cache` may run earlier in the chain; the load-bearing constraint is wake-after-effectors and wake-before-derivations.)
- [ ] `RunSupervisor` reference is threaded into `handle_transition` via the existing route-handler-supplies-app-state pattern (mirror how `registry` and `settings` are passed). When `supervisor` is `None`, the wake step is skipped — keeps test fixtures that don't care about dispatches free of supervisor boilerplate.
- [ ] Webhook arrives before dispatch row is committed (race covered in §9 of the brief): the wake step finds no row and no-ops; on next runtime-loop iteration the dispatch's commit sees the aux row already materialized via outbox and the runtime advances. Verified by T-236.
- [ ] Unit tests: wake-on-match, no-match-no-op, already-terminal-no-op, pipeline-order assertion (mocked collaborators counted in call order).

**Files to Modify/Create:**
- `src/app/modules/ai/lifecycle/reactor.py` — add `_wake_dispatch`, thread supervisor through.
- `src/app/modules/ai/router.py` — supply `app.state.run_supervisor` to the reactor call site at the engine webhook route.
- `tests/modules/ai/lifecycle/test_reactor_wake_dispatch.py` — new.

**Technical Notes:**
The dispatch lookup uses `correlation_id` as the join key — the same value the outbox row was keyed on, encoded into `triggered_by` by `engine_client`. No schema change. The dispatch row carries the correlation id either in `intake` (set at dispatch time by `EngineExecutor`) or in a dedicated column — pick the simpler one and document in the design doc. Recommendation: add `correlation_id` to the `Dispatch` `intake` JSONB rather than a new column, since the outbox row is the durable source of truth for the correlation.

---

### T-234: Trace shape — `executor_call` extended for engine mode

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-231

**Description:**
Extend the `trace_kind="executor_call"` schema so engine-mode dispatches emit `mode=engine`, `transition_key`, `correlation_id`, and `engine_run_id`. An operator can then join `executor_call` → `pending_aux_write` (by correlation id) → `webhook_event` (by correlation id) → step terminal.

The trace shape change is **additive** — existing local/remote/human entries are unchanged. The new fields are present only when `mode="engine"`.

**Rationale:**
AC-6. Forensic joinability is the load-bearing observability property; without correlation id in the trace, an operator has to reconstruct it from the webhook payload, which defeats the point of having a structured trace.

**Acceptance Criteria:**
- [ ] Trace writer accepts the new fields when present; rejects them on non-engine modes (defensive — prevents accidental schema drift).
- [ ] `EngineExecutor` populates all four fields on every dispatch envelope (success and failure paths both carry `transition_key` + `correlation_id`; `engine_run_id` is omitted on pre-engine-call failures).
- [ ] Unit test asserts a happy-path engine dispatch produces a trace entry with the four fields populated.
- [ ] `data-model.md` carries a note (no schema change, just a documentation update on the trace shape extension).

**Files to Modify/Create:**
- `src/app/modules/ai/trace.py` (or wherever `executor_call` was added in T-213) — schema extension.
- `src/app/modules/ai/schemas.py` — `DispatchEnvelope` carries the new fields (optional, engine-mode only).
- `tests/modules/ai/test_trace_executor_call_engine.py` — new.
- `docs/data-model.md` — note + changelog entry.

---

## Operational

### T-235: Restart reconciler — `reconcile-dispatches` extension for engine dispatches

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-231

**Description:**
Extend `src/app/modules/ai/executors/reconcile.py` (or add a sibling) so the lifespan-time reconciler handles engine-mode dispatches differently from local/remote/human. For each orphaned `Dispatch` with `mode="engine"`:

1. Look up the matching `PendingAuxWrite` row by correlation id.
2. Query the engine for the entity's current state via `lifecycle_client.get_item_state(...)` (or equivalent).
3. If the expected transition has occurred (engine state matches `transition_key.target`), materialize the aux row + mark the dispatch `failed` with `detail="reconciled_post_restart"` (the run owner is gone, so wake is meaningless — but the aux row must not be lost).
4. If the engine has not transitioned, mark the dispatch `failed` with `detail="orchestrator_restart_engine_unconfirmed"`.

Add a `uv run orchestrator reconcile-dispatches [--since=24h] [--dry-run]` CLI command (companion to the existing `reconcile-aux` from FEAT-008). Idempotent — safe to re-run.

**Rationale:**
AC-2. Restart safety is the whole point of the outbox pattern. A crash mid-dispatch (engine call sent, webhook not yet arrived) must not leak an orphan dispatch row indefinitely; the reconciler is the only path that retroactively settles such rows.

**Acceptance Criteria:**
- [ ] `reconcile_orphan_dispatches` (or a new sibling) special-cases `mode="engine"` rows: queries the engine, settles per the rules above.
- [ ] Non-engine modes preserve the FEAT-009 conservative-cancel behavior bit-for-bit.
- [ ] `uv run orchestrator reconcile-dispatches` CLI command added (mirroring the FEAT-008 `reconcile-aux` shape).
- [ ] `--dry-run` prints the action plan without writes.
- [ ] `--since=<duration>` bounds the lookback; default is the existing `EXECUTOR_RESTART_LOOKBACK_HOURS` from FEAT-009.
- [ ] Trace emits one `executor_call` finalization entry per reconciled dispatch (preserves FEAT-009 trace contract).
- [ ] Integration test: simulate a crash by writing a `dispatched` engine row + matching outbox row + no webhook, run the reconciler against a respx-stubbed engine that reports the transition has occurred, assert the aux row materializes and the dispatch is marked `failed` with `detail="reconciled_post_restart"`.
- [ ] Integration test (negative): same setup but engine reports the transition has not occurred — assert the dispatch is marked `failed` with `detail="orchestrator_restart_engine_unconfirmed"` and the outbox row remains for a future retry.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/reconcile.py` — engine-mode special case.
- `src/app/cli.py` — `reconcile-dispatches` command.
- `tests/integration/test_dispatch_reconciler_engine.py` — new.

**Technical Notes:**
The "query the engine for current state" path needs an engine endpoint that returns an item's current status. If `FlowEngineLifecycleClient` does not expose one today, add a thin `get_item_state(entity_id) -> str` method backed by the existing engine read API — out of scope to add a new engine-side endpoint. If even that read API is absent, scope the reconciler to *only* the second branch (mark `failed` with `detail="orchestrator_restart_engine_unconfirmed"`) and file a follow-on bug — surface this in the implementation plan when T-235 is picked up.

---

## Testing

### T-236: Integration — throwaway engine-bound test agent reaches terminal

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-232, T-233

**Description:**
End-to-end test under `tests/integration/`: declare a throwaway `test-agent@0.1.0` with two nodes — `request_seed_load` (local executor) and `request_engine_transition` (engine executor bound to `work_item.W2` — `in_progress` → `review`). Run the agent against a respx-stubbed engine that:

1. Receives the `transition` POST.
2. Asynchronously POSTs `item.transitioned` to `/hooks/engine/lifecycle/item-transitioned` with the matching correlation id.

Assert: dispatch row transitions `dispatched` → `completed`; aux row materializes; reactor pipeline order is correct; supervisor wake fires; runtime advances to terminal; `executor_call` trace entry carries `mode=engine` + `transition_key` + `correlation_id`.

Also includes the deliberate-ordering-inversion variant from §9 of the brief: webhook arrives before the dispatch row is visible — assert the wake step no-ops, the runtime advances on its next iteration via the materialized aux row, and the run reaches terminal anyway.

**Rationale:**
AC-1 proof. This is the load-bearing integration test — the throwaway agent exercises every leg of FEAT-010 end-to-end with no FEAT-011 dependency.

**Acceptance Criteria:**
- [ ] Test in `tests/integration/test_engine_executor_e2e.py`.
- [ ] Agent YAML lives at `tests/fixtures/agents/test-agent@0.1.0.yaml` (test-only fixture; not registered in `agents/`).
- [ ] Run reaches `done_node`; the engine `executor_call` trace entry shows `mode=engine` and the four engine-mode fields.
- [ ] Race variant: webhook delivered before dispatch row commits — run still reaches terminal; wake-step no-op is observed in the trace/log.
- [ ] HMAC signature on the inbound webhook is computed via the production helper.

**Files to Modify/Create:**
- `tests/integration/test_engine_executor_e2e.py` — new.
- `tests/fixtures/agents/test-agent@0.1.0.yaml` — new.

---

### T-237: Structural test — `executors/engine.py` import quarantine

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-231

**Description:**
Static guard test, sibling to `tests/test_runtime_loop_is_pure.py` (FEAT-009 / T-228). Asserts:

1. `runtime_deterministic.py` does **not** import `executors.engine` (directly or transitively).
2. `executors/engine.py` does **not** import `FlowEngineLifecycleClient` at module scope — only via `TYPE_CHECKING` block.
3. The import-time module list, after importing `runtime_deterministic`, does not include `app.modules.ai.lifecycle.engine_client` or `httpx` from the engine path.

Encodes AC-7 as a permanent property.

**Rationale:**
AC-7. The whole point of the executor seam is that the runtime loop is unaware of which mode is on the other side. A transitive import of `engine_client` into `runtime_deterministic` would regress that property silently.

**Acceptance Criteria:**
- [ ] Test exists, fails on a deliberate violation (commented-out negative case in the file), passes on real code.
- [ ] Test runs in CI as part of the standard suite.

**Files to Modify/Create:**
- `tests/test_engine_executor_is_isolated.py` — new.

---

### T-238: Integration — `lifecycle-agent@0.1.0` LLM-policy + engine path unchanged

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-233

**Description:**
Run the existing `lifecycle-agent@0.1.0` LLM-policy + engine integration test suite after the reactor pipeline extension lands. No edits to the test, no edits to the v0.1.0 YAML, no edits to the v0.1.0 tool source. Asserts AC-3 directly — the v0.1.0 path through `FlowEngineLifecycleClient` directly still works.

**Acceptance Criteria:**
- [ ] Existing test files exercising v0.1.0 + engine pass with zero modifications.
- [ ] Reactor trace shape preserved (no missing or unexpected pipeline steps observed against the v0.1.0 baseline).
- [ ] No `Dispatch` rows created for the v0.1.0 path (v0.1.0 does not register engine executors; the wake step never fires).

**Files to Modify/Create:**
- None — this task is "run the existing suite." Failure here is a bug in T-233.

---

### T-239: Coverage validator — engine-bound nodes covered or exempted

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-232

**Description:**
Extend the FEAT-009 coverage validator tests so a deterministic agent declaring a node intended for engine dispatch must either register an `EngineExecutor` *or* carry an explicit `no_executor("≥10-char reason")` exemption. Same enforcement bar as local/remote/human — the validator does not distinguish modes, so this is a test-only task to confirm the existing validator already handles the new mode correctly.

**Rationale:**
AC-4. The coverage validator is mode-agnostic by design; this task is the proof, not new validator logic.

**Acceptance Criteria:**
- [ ] Test fixture: a deterministic agent declaring an engine-bound node with no registration → lifespan startup raises `ExecutorCoverageError` naming the unbound `(agent_ref, node_name)`.
- [ ] Same agent with `no_executor("transitions are skipped in dev mode")` exemption → lifespan startup succeeds.
- [ ] Same agent with `register_engine_executor(...)` → lifespan startup succeeds.

**Files to Modify/Create:**
- `tests/modules/ai/executors/test_coverage_engine.py` — new.

---

## Polish & docs

### T-240: Docs realignment — CLAUDE.md, ARCHITECTURE.md, api-spec.md

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-233, T-235

**Description:**
Walk the project docs and propagate the FEAT-010 shape:

- `CLAUDE.md` — Patterns: add "Engine-bound nodes register an `EngineExecutor`, never call the engine inline." Anti-Patterns: add "Don't add a parallel persistence surface for engine round-trips — reuse `pending_aux_writes` and the FEAT-008 reactor." Update the Quick-Reference directory map under `modules/ai/executors/` with `engine.py`. Update the reactor pipeline ordering note to: `materialize aux → consume correlation context → fire effectors → wake dispatch → fire derivations`.
- `ARCHITECTURE.md` — extend the executor seam diagram to include the engine path; show the reactor-wake leg; changelog entry referencing FEAT-010.
- `api-spec.md` — note that `/hooks/lifecycle/transitions` now also wakes engine-mode dispatches; payload unchanged. Document the new `reconcile-dispatches` CLI in the operational section if applicable.
- `data-model.md` — confirm no schema changes (the trace-shape note from T-234 is the only doc-touch here).

**Acceptance Criteria:**
- [ ] All four docs updated where applicable; each carries a 2026-04-26 (or current) changelog entry referencing FEAT-010.
- [ ] No doc reference to engine-bound producer logic surviving without a path through `EngineExecutor` after FEAT-010.
- [ ] `CLAUDE.md` Pre-Work Checklist remains valid (no broken file paths).
- [ ] The reactor pipeline ordering line in `CLAUDE.md` matches the implementation in `reactor.py` after T-233.

**Files to Modify/Create:**
- `CLAUDE.md`
- `docs/ARCHITECTURE.md`
- `docs/api-spec.md`
- `docs/data-model.md` — note only.

---

## Summary

**Total task count: 11** (T-230 through T-240).

By type:
- Backend: 4 (T-231, T-232, T-233, T-234, T-235 — T-235 is partly CLI/operational)
- Testing: 4 (T-236, T-237, T-238, T-239)
- Documentation: 2 (T-230, T-240)

Complexity distribution:
- S: T-230, T-232, T-234, T-237, T-238, T-239, T-240
- M: T-233, T-235, T-236
- L: T-231
- XL: none.

**Critical path** (longest dependency chain — also the recommended landing order):
T-230 → T-231 → T-233 → T-236

That is: design doc → engine executor → reactor wake → end-to-end proof.

T-232 (bootstrap helper) and T-234 (trace shape) and T-237 (import quarantine) can land in parallel with the early steps. T-235 (reconciler) sits on T-231. T-238 + T-239 (validator + v0.1.0 unchanged) and T-240 (docs sweep) are the closing tasks.

**Risks / open questions**

- **`FlowEngineLifecycleClient.get_item_state` may not exist.** T-235's reconciler needs to query the engine for an item's current state. If the client lacks that method, adding a thin read-only wrapper is in scope; adding a new engine-side endpoint is not. The implementation plan for T-235 should flag this on pickup.
- **Where does `correlation_id` live on `Dispatch`?** T-233 recommends carrying it in `Dispatch.intake` JSONB rather than adding a new column. The design doc (T-230) should make this call explicitly so reviewers don't relitigate it in T-231.
- **Engine-absent dev mode.** `EngineExecutor` requires `lifecycle_client`; v0.1.0 dev mode without a configured engine will refuse to register engine executors. T-232's helper raises a clear error in that case — but it means an FEAT-011 deterministic agent cannot run in fully engine-absent mode. Acceptable for v0.5.0; revisit if a real consumer demands a no-engine fallback.
- **Multi-target transitions on engine-bound nodes.** The brief is silent on whether `FlowResolver` branch rules can dispatch differently based on `result.engine_to_status`. Recommendation: in scope for FEAT-011, out of scope for FEAT-010 — T-236's throwaway agent uses 1→1 transitions only.
