# Feature Brief: FEAT-010 — Engine executor adapter

> **Purpose**: Provide the missing seam between FEAT-009's deterministic dispatch model and FEAT-006/008's flow-engine integration. Today, deterministic agents can only dispatch to local/remote/human executors that produce data — none of them advance the engine's authoritative work-item or task workflows. This FEAT adds an `EngineExecutor` that maps a node dispatch to a flow-engine workflow transition (with correlation-id encoding, outbox enqueue, and webhook round-trip), so a `flow.policy: deterministic` agent can drive the engine the same way `lifecycle-agent@0.1.0` does today through `FlowEngineLifecycleClient`.
>
> **Relationship to FEAT-009.** FEAT-009 stood up the executor registry and the local/remote/human adapters. The deterministic runtime can resolve and dispatch, but it has no executor that *advances engine state*. Without this, FEAT-011's deterministic lifecycle port can't actually drive work-item / task transitions — it would just produce data with no authoritative state machine behind it.
>
> **Relationship to FEAT-008.** FEAT-008 made the engine the authority for lifecycle state and standardized the outbox + reactor pipeline. This FEAT does **not** invent a parallel pipeline — it routes engine-bound dispatches through the same `PendingAuxWrite` outbox and the same `lifecycle/reactor.py` consumer. The `EngineExecutor` is the *dispatch-shaped* entry into that pipeline.
> **Template reference**: `.ai-framework/templates/feature-brief.md`

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-010 |
| **Name** | Engine executor adapter |
| **Target Version** | v0.5.0 |
| **Status** | Not Started |
| **Priority** | High |
| **Requested By** | Project owner (FEAT-009 closing review — deterministic runtime can dispatch but cannot advance engine state) |
| **Date Created** | 2026-04-26 |

---

## 2. User Story

**As an** orchestrator operator declaring a deterministic agent that owns work-item or task lifecycle, **I want** a registered executor that maps a node dispatch to a flow-engine workflow transition (W1–W6, T1–T12) **so that** I can write deterministic agents that drive the authoritative engine state without re-implementing the FEAT-008 outbox + reactor pipeline inline.

---

## 3. Goal

A reusable `EngineExecutor` registered against `(agent_ref, node_name)` that, on dispatch, encodes a fresh correlation id, enqueues a `PendingAuxWrite` row in the same transaction that calls the flow engine's transition endpoint, returns `dispatched`, and is woken by the existing `lifecycle/reactor.py` when the engine echoes the correlation id back. The `EngineExecutor` is to lifecycle transitions what `RemoteExecutor` is to remote HTTP work — a uniform dispatch shape over the existing async confirmation pattern.

---

## 4. Feature Scope

### 4.1 Included

- A new `src/app/modules/ai/executors/engine.py` defining `EngineExecutor(transition_key, lifecycle_client, ...)` implementing the FEAT-009 `Executor` Protocol. Constructor parameters bind a single transition key (e.g. `"work_item.W4"` or `"task.T6"`); one executor instance per `(agent_ref, node_name)`.
- Dispatch behavior: open a session via `session_factory`, generate a correlation id, write `PendingAuxWrite` row + call `FlowEngineLifecycleClient.transition(...)` in the same transaction, commit, return `Dispatch.state = DISPATCHED`. The supervisor's per-dispatch future is later resolved by the reactor when the matching `item.transitioned` webhook arrives.
- A reactor extension: when the reactor processes a webhook whose correlation id was emitted by an `EngineExecutor`, it calls `supervisor.deliver_dispatch(dispatch_id, result)` after materializing aux rows — so the deterministic runtime advances on the same wake that effectors fire on. Today the reactor wakes signal listeners only; this FEAT adds the dispatch-listener wake-up.
- Bootstrap helper `register_engine_executor(registry, agent_ref, node_name, transition_key)` for use in `executors/bootstrap.py` so FEAT-011's v0.3.0 agent can wire each engine-bound node in one line.
- Reuse of FEAT-008's `pending_aux_writes` table, outbox model, and reconciler — no new persistence surface. `EngineExecutor` is a *producer of outbox rows*, not a parallel mechanism.
- Trace coverage: every engine dispatch emits the standard `executor_call` entry plus a correlation-id field so an operator can join `executor_call` → `pending_aux_write` → `webhook_event` → `step.complete`.
- A `reconcile-dispatches` companion to `reconcile-aux`: at lifespan startup, find dispatches in state `DISPATCHED` whose run is no longer running and whose correlation id has no matching webhook, and either mark them `FAILED` (run owner gone) or re-await (run resuming). This closes the restart-safety gap FEAT-009 deferred.
- Documentation: `CLAUDE.md` Pattern entry "Engine-bound nodes register an `EngineExecutor`, never call the engine inline"; `ARCHITECTURE.md` updates the executor seam diagram to include the engine path; `api-spec.md` notes that dispatches with `mode=engine` settle via the existing `/hooks/lifecycle/transitions` webhook (no new endpoint).

### 4.2 Excluded

- **The deterministic lifecycle port itself.** That's FEAT-011. This FEAT ships the seam and a unit-test agent that exercises a single engine transition — not a re-expression of the eight-tool lifecycle.
- **Engine-side workflow changes.** Existing FEAT-006 work-item and task workflows are unchanged. If FEAT-011 needs new transitions, those are scoped there.
- **Replacing `FlowEngineLifecycleClient`.** The new executor wraps the existing client; no new HTTP surface, no new auth.
- **A non-lifecycle engine integration.** This FEAT targets the existing lifecycle workflows only. Generalizing to arbitrary engine workflows is a future FEAT once a second consumer demands it.
- **Aux-write semantics changes.** FEAT-008's reactor materializes aux rows from outbox. That stays. The only addition is the dispatch-wake hook on reactor completion.

---

## 5. Acceptance Criteria

- **AC-1**: An `EngineExecutor` registered for `(test-agent@0.1.0, transition_node)` against transition `work_item.W2` (in_progress → review) successfully advances a seeded work item end-to-end: dispatch → outbox row → engine transition → webhook → reactor → supervisor wake → step terminal. Verified by integration test with `respx` stubbing the engine.
- **AC-2**: A run whose process restarts mid-dispatch (engine call sent, webhook not yet arrived) is recovered by `reconcile-dispatches` at lifespan startup: the orphaned dispatch is either resolved from the engine's current state or marked `FAILED` with a structured reason. No row is ever silently dropped.
- **AC-3**: The existing `lifecycle-agent@0.1.0` LLM-policy + engine integration test suite continues to pass unchanged. `EngineExecutor` is additive; the v0.1.0 path through `FlowEngineLifecycleClient` directly still works.
- **AC-4**: Coverage validation refuses to boot if a deterministic agent declares an engine-bound node without registering an `EngineExecutor` *or* an explicit `no_executor("≥10-char reason")` exemption — same enforcement bar as FEAT-009's local/remote/human paths.
- **AC-5**: The reactor's existing aux-row materialization (Approval, TaskAssignment, TaskPlan, TaskImplementation) is unchanged. The new dispatch-wake step runs *after* aux materialization completes — order: `materialize aux → consume correlation context → fire effectors → wake dispatch → fire derivations`. Verified by ordering test.
- **AC-6**: `trace_kind="executor_call"` entries for engine dispatches include `mode=engine`, `transition_key`, `correlation_id`, and `engine_run_id`, sufficient to join through to the matching webhook event.
- **AC-7**: A structural test asserts `executors/engine.py` imports `FlowEngineLifecycleClient` only via constructor injection — never at module scope — preserving the FEAT-009 import-quarantine discipline for the deterministic runtime.

---

## 6. Key Entities and Business Rules

| Entity | Role in Feature | Key Business Rules |
|--------|----------------|--------------------|
| `Dispatch` | Receives `mode=engine`; settles via reactor wake instead of webhook on `/hooks/executors/<id>` | Must remain in `DISPATCHED` until correlation id round-trips; reconciler is the only path that retroactively marks `FAILED` |
| `PendingAuxWrite` | Bridges engine call → aux materialization (FEAT-008 contract, unchanged) | Correlation id is the single join key between dispatch, outbox, webhook, and aux row |
| `WebhookEvent` | Existing engine `item.transitioned` event; reactor extends to wake the matching dispatch future | Persist-first ordering preserved; dispatch wake is downstream of aux materialization |
| `EngineWorkflow` (cache) | Read-only by `EngineExecutor`; tenant-scoped per BUG-002 | Stale-cache 404 recovery path applies (re-register on miss) |

**New entities required:** None. FEAT-010 reuses FEAT-008's persistence surface entirely.

---

## 7. API Impact

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/hooks/lifecycle/transitions` | POST | Existing | Reactor extends to wake matching dispatch futures after aux materialization — no payload change |
| `/api/v1/runs/{id}/dispatches` | GET | New (optional) | Operator visibility into in-flight dispatches; nice-to-have, can defer |

**New endpoints required:** None required for AC-1 through AC-7. `GET /api/v1/runs/{id}/dispatches` is a stretch for ops visibility.

---

## 8. UI Impact

N/A — headless service.

---

## 9. Edge Cases

- **Engine call succeeds, outbox commit fails.** Outbox + engine call must be in the same transaction; if commit fails, the dispatch must transition to `FAILED` and the engine call must not have been made. Use the existing FEAT-008 transactional pattern.
- **Webhook arrives before dispatch row is committed.** Reactor must tolerate: it's already idempotent on correlation id; the wake-up is a no-op if the dispatch isn't yet visible (the dispatch's commit will see the aux row already materialized via the outbox row it wrote, and the runtime advances on next iteration). Verified by integration test with deliberate ordering inversion.
- **Engine returns 4xx on transition.** Mark dispatch `FAILED` with structured reason; runtime advances to error state per `_DISPATCH_TRANSITIONS`. Do not retry — engine 4xx is a contract violation, not a transient.
- **Engine returns 5xx / timeout.** Bounded retry per existing `FlowEngineLifecycleClient` retry policy. After exhaustion, mark `FAILED`. No outbox row is committed unless the engine accepted the call.
- **Process restart between engine call and webhook.** Reconciler queries engine for the work item's current state; if the expected transition has occurred, materialize the aux row + wake the dispatch (if the run is resumable) or mark `FAILED` (if the run is gone).
- **Same correlation id reused (bug).** UNIQUE constraint on `pending_aux_writes.correlation_id` already prevents double-dispatch; surface as `IllegalDispatchTransition`.
- **Tenant misconfigured / workflow cache stale.** BUG-002 fix already covers tenant scoping; stale-cache 404 path triggers re-register and one retry.

---

## 10. Constraints

- Must not introduce a new persistence surface — reuse `pending_aux_writes` and the FEAT-008 reactor.
- Must not add a new webhook endpoint — engine round-trip stays on `/hooks/lifecycle/transitions`.
- Must not call the engine from inside the deterministic runtime loop module — only from the executor module, via constructor-injected client.
- Must preserve FEAT-009's import quarantine: `runtime_deterministic.py` does not import `executors/engine.py`.
- Must work under single-worker mode (matches existing AD constraint); cross-worker dispatch coordination is out of scope.

---

## 11. Motivation and Priority Justification

**Motivation:** FEAT-009 shipped the executor seam but the only registered executors are *data producers* (local Python callables, remote HTTP, human signals). The lifecycle agent's reason for existing — driving authoritative work-item and task state in the flow engine — has no deterministic-mode equivalent today. Without FEAT-010, FEAT-011 cannot start: there's nothing to dispatch *to* for an engine transition.

**Impact if delayed:** `lifecycle-agent@0.1.0` (LLM-policy) remains the only path that drives the engine. The FEAT-009 architecture stays unproven against the actual production workload. Every new agent that needs engine state has to either fork the v0.1.0 path or wait.

**Dependencies on this feature:** FEAT-011 (deterministic lifecycle port) depends on this directly. FEAT-012 (aux writes from executors) depends transitively — its outbox writes flow through the same `EngineExecutor` path.

---

## 12. Traceability

| Reference | Link |
|-----------|------|
| **Persona** | Orchestrator operator (sole persona in v1) |
| **Stakeholder Scope Item** | Headless agent loop drives `carestechs-flow-engine` over HTTP |
| **Success Metric** | Lifecycle-agent runs reach closure without manual intervention |
| **Related Work Items** | FEAT-006 (deterministic lifecycle flow), FEAT-008 (engine as authority), FEAT-009 (orchestrator as pure orchestrator), FEAT-011 (next), BUG-002 (engine_workflows tenant scope) |

---

## 13. Usage Notes for AI Task Generation

1. **Scope enforcement:** Section 4.1 only. No re-expression of the eight-tool lifecycle in this FEAT — that's FEAT-011.
2. **Reuse FEAT-008 surfaces:** `pending_aux_writes`, reactor, `FlowEngineLifecycleClient`, correlation-id helpers. New persistence is a smell.
3. **Reuse FEAT-009 surfaces:** `Executor` Protocol, `ExecutorRegistry`, `Dispatch` model, supervisor primitives. The new executor *fits the existing seam*.
4. **Test against `respx`-stubbed engine** for unit + integration; live engine smoke is a nice-to-have.
5. **Reconciler is part of the FEAT, not optional.** AC-2 is non-negotiable — restart safety is the whole point of the outbox pattern.
6. **Trace before code:** the `executor_call` trace shape extension (correlation_id + transition_key) is a contract — generate tasks for the trace shape *before* the dispatch path so the integration test can assert on it.
