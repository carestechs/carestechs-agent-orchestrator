# Feature Brief: FEAT-008 — Effector Registry + Engine-as-Authority

> **Purpose**: Realign the orchestrator with its architectural vision (stakeholder-definition §"Architectural Position"): the engine is the authoritative private backend, the orchestrator is the sole gateway, and **every state transition fires effectors**. Supersedes the FEAT-006 rc2 closeout ADR.
> **Template reference**: `.ai-framework/templates/feature-brief.md`

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-008 |
| **Name** | Effector Registry + Engine-as-Authority |
| **Target Version** | v0.7.0 |
| **Status** | Not Started |
| **Priority** | High |
| **Requested By** | Tech Lead (architectural drift detected post-FEAT-007) |
| **Date Created** | 2026-04-21 |

---

## 2. User Story

**As an** operator running the orchestrator against a real flow engine, **I want to** see the engine own state and the orchestrator react to transitions with outbound effectors (GitHub checks, assignment notifications, task generation, etc.), **so that** the orchestrator stops duplicating engine responsibilities and the product value sits where it belongs — in the integration + reactor layer, not in the state store.

---

## 3. Goal

Move the orchestrator from **driver (writes state, engine mirrors)** to **gateway + reactor (writes to engine, reacts to engine webhooks)**: drop or demote local state columns to caches, migrate aux-row writes into a correlation-matched reactor, and introduce a first-class effector registry so every state transition has a named outbound action (even if that action is `log-only` in v1).

---

## 4. Feature Scope

### 4.1 Included

- **Supersede the FEAT-006 rc2 closeout ADR.** Write a new ADR (`docs/design/feat-008-engine-as-authority.md`) that inverts the phase-2 decision: engine is sole writer of state, orchestrator aux-row writes move to the reactor, state columns become caches.
- **Effector registry.** A pluggable registry keyed on `(entity_type, transition | entry_state | exit_state)` that the reactor dispatches on every `item.transitioned` webhook. Protocol `Effector` with `async def fire(context) -> EffectorResult`.
- **Observability for effectors.** Every fire writes a trace entry (`trace_kind="effector_call"`, `effector_name`, `entity_id`, `result`, `latency_ms`, error). Structured log with `run_id`/`entity_id` in contextvars.
- **Relocate GitHub check create/update into effectors.** Today's inline `_post_create_check` / `_post_update_check` in `lifecycle/service.py` move to the registry. Behavior unchanged; the seam moves.
- **Implement assignment-request effector.** On task entering `assigning` (transition T4 entry), fire a `request_assignment` effector. v1 transport: **structured log + trace** (pluggable transport layer deferred). Proves the seam; unblocks a future Slack/email adapter without re-architecture.
- **Replace task-generation stub with a real effector.** On work item entering `pending_tasks` (transition W1 entry), fire `generate_tasks`. v1 implementation: inline rules-based generator that reads the work-item brief and proposes tasks (deterministic; no LLM). Same seam will later host an LLM-agent-backed generator.
- **Migrate aux-row writes to the reactor.** `Approval`, `TaskAssignment`, `TaskPlan`, `TaskImplementation` are written when the engine confirms the transition via correlation-matched webhook, not inline in the signal adapter. Closes phase-2 of FEAT-006 rc2.
- **Drop `locked_from` / `deferred_from` columns.** Engine transition history is authoritative.
- **Demote `work_items.status` / `tasks.status` to read-through cache.** Writes only from the reactor on webhook arrival. Signal adapters no longer mutate `status`.
- **Outbox + reconciliation backstop.** If an engine webhook is lost, aux rows never land. A lightweight outbox: after the signal adapter forwards to the engine, also enqueue a `pending_aux_write` record keyed on correlation id. A sync job (opt-in, runnable from CLI) reconciles orphans hourly and on startup.
- **Test-harness helper.** `await_reactor(session, entity_id, predicate)` utility so integration tests can wait for the reactor to land aux rows before asserting. Replaces the current "aux rows exist synchronously after signal" pattern.

### 4.2 Excluded

- **Slack / Jira / email transports for effectors.** The registry lands with log-only transport. Concrete integrations are follow-on FEATs — each adds an effector adapter, not architecture.
- **A UI.** Out of product scope per stakeholder-definition; operators still drive via HTTP.
- **LLM-backed task generation.** V1 replaces the stub with a deterministic inline generator. Agent-backed generation follows once the effector seam is proven and the lifecycle-agent integration work is scoped separately.
- **Dropping HTTP signal endpoints.** They remain the ingress surface — the architecture requires them. This FEAT only changes what happens *after* the signal is received.
- **Multi-worker / distributed supervisor.** Out of scope as before.
- **Replacing FEAT-007's GitHub check with a new implementation.** Behavior stays identical; only the code location moves.

---

## 5. Acceptance Criteria

- **AC-1**: `Effector` protocol + `EffectorRegistry` live in `src/app/modules/ai/lifecycle/effectors/`. Registry resolves `(entity_type, transition_or_state)` to zero or more effectors and fires them in a defined order.
- **AC-2**: New ADR `docs/design/feat-008-engine-as-authority.md` formally supersedes `feat-006-rc2-architectural-position.md`. The older ADR gains a "Superseded by FEAT-008" banner.
- **AC-3**: Every lifecycle transition that touches external state has at least one registered effector, or an explicit `@no_effector("reason")` decorator acknowledging the gap. No silent transitions.
- **AC-4**: `github_check_create` + `github_check_update` effectors replace the inline calls; all FEAT-007 tests still pass unchanged (behavior preservation).
- **AC-5**: `request_assignment` effector fires on every `task: assigning` entry. Trace row recorded. Transport is log-only but the effector contract is pluggable.
- **AC-6**: `generate_tasks` effector replaces `dispatch_task_generation` stub and produces at least 1 `Task` row per call against a fixture work item.
- **AC-7**: Aux rows (`Approval`, `TaskAssignment`, `TaskPlan`, `TaskImplementation`) are written by the reactor on correlation-matched webhook, not by the signal adapter. Unit test: stub the engine out, signal adapter returns 202, aux rows are absent; deliver a synthetic webhook, reactor writes them.
- **AC-8**: `work_items.locked_from` + `tasks.deferred_from` are dropped (migration). Lock/unlock signals no longer write these columns.
- **AC-9**: `work_items.status` + `tasks.status` are updated only by the reactor. A service-layer test proving "signal adapter does not touch status" exists.
- **AC-10**: Outbox table `pending_aux_writes` exists. Reconciliation job (`uv run orchestrator reconcile-aux`) matches orphan correlation ids against recent engine state and writes any missing aux rows. Must be idempotent.
- **AC-11**: Every effector fire emits a `trace_kind="effector_call"` entry with `effector_name`, `entity_id`, `duration_ms`, `status` (`ok`/`error`), and `error_code` on failure.
- **AC-12**: Integration test `test_feat008_reactor_authoritative.py` proves the full FEAT-006 flow still completes, with status columns populated only via reactor and aux rows written only via reactor.

---

## 6. Key Entities and Business Rules

No new primary entities. Shape changes to existing ones:

| Entity | Change |
|--------|--------|
| `WorkItem` | Drop `locked_from`. Demote `status` to reactor-managed cache. |
| `Task` | Drop `deferred_from`. Demote `status` to reactor-managed cache. |
| `Approval` | Write path moves to reactor. Row shape unchanged. |
| `TaskAssignment`, `TaskPlan`, `TaskImplementation` | Same — write path moves to reactor. |
| `PendingSignalContext` | Becomes load-bearing: the correlation id is the only way the reactor knows which payload to materialize. Retention bumped. |
| `PendingAuxWrite` (new) | Outbox row for orphan reconciliation: `{correlation_id, signal_name, payload, enqueued_at, resolved_at}`. |

**Business rules:**

- **Reactor is the only writer of `status` and aux rows.** Signal adapters forward to the engine + commit the outbox + return 202. They do not mutate status or aux tables.
- **Effectors are fire-and-forget from the reactor's perspective.** A failing effector logs, traces, and moves on; it never blocks the next effector or the next transition. Retry is the outbox's job.
- **Reconciliation is idempotent.** Running `reconcile-aux` twice produces the same final state.
- **Effector ordering is deterministic.** Registry returns effectors in insertion order; documented, testable.

---

## 7. API Impact

No new endpoints. No DTO changes. **Behavioral change:** `POST /tasks/{id}/implementation` returns 202 *before* aux rows land; callers that assert on aux-row state must poll the task back or use the new `await_reactor` test helper. The caller-visible fields on `Task` and `WorkItem` DTOs are unchanged — the reactor backfills status before responses are read, via the read-through cache.

New CLI command: `uv run orchestrator reconcile-aux [--since=24h]` — drains orphans from the outbox.

---

## 8. UI Impact

None.

---

## 9. Edge Cases

- **Webhook lost.** Outbox catches it. Reconciliation resolves. Test: block the webhook receiver, fire a signal, assert outbox has the orphan, run reconciliation, assert aux row exists.
- **Webhook duplicate.** Reactor is idempotent on correlation id. Second arrival is a no-op with a trace entry.
- **Effector raises.** Trace records `status=error` with `error_code`. Next effector fires. Transition is not rolled back.
- **Effector registry misconfiguration.** A transition with no effector and no explicit `@no_effector` fails startup fast — composition root validates. Non-exhaustive check is a release blocker.
- **Engine-down for extended period.** Signal adapters still return 202 (outbox enqueued). No aux rows land until engine comes back. Reconciliation catches up. Operators see a growing `pending_aux_writes` count as a health signal.
- **Migration with live data.** Dropping `locked_from` / `deferred_from` is destructive. Migration script must verify: no currently-locked work items, no currently-deferred tasks whose `deferred_from` would be lost. If found, fail the migration and surface the rows for operator triage.

---

## 10. Constraints

- **No frontend.** Effector transports are log-only in v1.
- **Single-worker.** Reactor lives in-process. No distributed reactor until cross-worker coordination exists.
- **Flow engine must emit `item.transitioned` reliably enough that the outbox is a backstop, not the primary path.** If engine reliability drops below that bar, FEAT-008 cannot land.
- **No breaking API changes.** Caller-facing contracts (envelopes, DTO shapes) stay identical. The behavioral shift (aux rows not synchronous) is internal.
- **Composition integrity (AD-9).** Without engine config, the reactor path degrades: signal adapters write aux rows inline (pre-FEAT-008 behavior) and status columns stay authoritative. The engine-present path is the target; engine-absent is a dev-mode fallback.

---

## 11. Motivation and Priority Justification

**Motivation:** The stakeholder-definition clarification surfaced that the FEAT-006 rc2 closeout ADR was reasoning about the wrong architecture. Under "engine is a dead mirror" the ADR's conclusion holds; under "engine is the authoritative private backend" the conclusion inverts. We either fix the drift now, while the cost is two moving pieces (GitHub check + task generation), or accept that every future effector layer (Slack, email, UI notifications, automation triggers) gets built against the wrong seam.

**Impact if delayed:** Every new integration duplicates the effector plumbing we should have built once. The orchestrator continues growing inline side-effects in service adapters. The rc2 ADR's "inline is simpler" argument becomes load-bearing by accretion, making the pivot progressively more expensive.

**Dependencies on this feature:** Slack / Jira / email effectors, future agent-driven automation triggers, any read API for external consumers (who will want engine state, not orchestrator cache).

---

## 12. Traceability

| Reference | Link |
|-----------|------|
| **Persona** | `docs/personas/primary-user.md` |
| **Stakeholder Scope Item** | "Architectural Position" + "Effectors are the product" (stakeholder-definition §Product Philosophy) |
| **Success Metric** | Lifecycle stage automation coverage (every stage transition is observably effected). Composition integrity (engine-absent mode still runs). |
| **Related Work Items** | FEAT-006 (prerequisite — state machine + engine mirror). FEAT-007 (GitHub check, will be relocated). |
| **Superseded ADR** | `docs/design/feat-006-rc2-architectural-position.md` — its "phase-2 is the end state" conclusion is explicitly reversed by this work. |
| **Design Input** | `docs/stakeholder-definition.md` §"Architectural Position" (added 2026-04-21). |

---

## 13. Usage Notes for AI Task Generation

1. **Respect the "effectors are the product" rule.** Every transition that was previously a silent state change should land with a registered effector or an explicit `@no_effector("reason")` exemption. Review-time gate: a task list that produces silent transitions is incomplete.
2. **Relocate, don't reinvent.** FEAT-007's GitHub check already works. The task is to move it into the registry, not redesign it. Behavior preservation is a strict acceptance criterion.
3. **Start with the seam, then fill it in.** The registry + reactor dispatch + outbox + trace kind are the load-bearing pieces. The two concrete effectors (assignment-request, task-generation) are validation that the seam is real — don't let them bloat into separate subsystems.
4. **Tests change shape, not count.** Expect to rewrite FEAT-006 assertions that read aux rows synchronously. Use the `await_reactor` helper. Do not add `asyncio.sleep` polling loops.
5. **Migration is destructive.** `locked_from` / `deferred_from` dropped with data loss for locked/deferred rows. Task generation must include a pre-migration check task.
6. **Do not reintroduce engine-absent inline writes as the primary path.** The fallback exists; it does not define the shape.
7. **Outbox reconciliation is not optional.** Treat it as part of the critical path, not a "nice-to-have" task.
