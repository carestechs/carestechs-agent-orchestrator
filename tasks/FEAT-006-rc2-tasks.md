# Task Breakdown: FEAT-006 v0.6.0-rc2 — Engine-Backed State

> **Source:** `docs/work-items/FEAT-006-deterministic-lifecycle-flow.md` (re-opened post-rc1)
> **Context:** rc1 (`a29dcad`) shipped with state in the orchestrator's own Postgres — the flow engine was not in the loop. This breakdown realigns the implementation so the engine owns work-item + task state (the architectural intent per AD-1 and the design-doc model). Orchestrator keeps the richer audit + routing data.
> **Generated:** 2026-04-19
> **Prompt:** `.ai-framework/prompts/feature-tasks.md`

**8 tasks, IDs T-128 → T-135.** Critical path depth 7: T-128 → T-129 → T-131 → T-132 → T-133 → T-134 → T-135.

**Split of responsibility after realignment:**

| Stored in | Entity / Field |
|-----------|----------------|
| **Engine** (`carestechs-flow-engine`) | `work_item_workflow` definition, `task_workflow` definition, current state of every work item + task (as an "item" row inside each workflow), transition history (engine's audit log) |
| **Orchestrator** (this repo) | Everything else: `TaskAssignment`, `Approval`, `TaskPlan`, `TaskImplementation`, `LifecycleSignal`, plus `work_items` + `tasks` rows reduced to identity + `engine_item_id` FK |

**Key invariant change:** signal handlers become two-phase. Phase 1: call engine's `POST /api/items/{id}/transitions` (engine validates legality + records history). Phase 2: engine emits a webhook back to the orchestrator → handler fires W2/W5/T4 derivations, writes `Approval` rows, runs idempotency bookkeeping. In the transaction that receives the webhook, not in the signal's request handler.

Every task is `Workflow: standard`.

---

## Foundation

### T-128: Engine HTTP client

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** None

**Description:**
Thin `httpx.AsyncClient` wrapper around the four endpoints FEAT-006 needs: `POST /api/workflows`, `POST /api/workflows/{id}/items`, `POST /api/items/{id}/transitions`, `POST /api/webhook-subscriptions`. JWT bearer auth — token acquired once at startup via `POST /api/auth/token` and cached. Retries on 5xx/timeout; 4xx surfaces as typed exceptions.

**Rationale:**
Every engine interaction routes through this client. Isolating it makes mocking with `respx` trivial in tests.

**Acceptance Criteria:**
- [ ] `FlowEngineLifecycleClient` class in `src/app/modules/ai/lifecycle/engine_client.py` with typed methods for the four endpoints.
- [ ] JWT token caching with expiry check; re-auth transparently on 401.
- [ ] Bounded retry (3 attempts, 500 ms → 4 s backoff + jitter) on 5xx / connection / timeout.
- [ ] `EngineError` raised on 4xx with status code + body preserved.
- [ ] Unit tests with `respx` cover each endpoint's happy path, 401 re-auth, 5xx retry, 400 surface.
- [ ] `uv run pyright`, `ruff`, tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/lifecycle/engine_client.py` — new.
- `src/app/config.py` — add `flow_engine_base_url`, `flow_engine_tenant_api_key` (SecretStr).
- `tests/modules/ai/lifecycle/test_engine_client.py` — new.

**Technical Notes:**
Reuse the existing `app.modules.ai.engine_client.FlowEngineClient` pattern — it already solves the httpx + retry shape. Don't merge the two: the existing client targets the flow engine's run/dispatch surface (FEAT-002), this one targets the lifecycle/item surface (FEAT-006). Same repo, different endpoints.

---

### T-129: Workflow bootstrap on startup

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-128

**Description:**
On app startup, ensure the two workflows exist in the engine for this tenant: `work_item_workflow` (states `open`, `in_progress`, `locked`, `ready`, `closed` with the edges from the design doc) and `task_workflow` (9 states with forward + rejection + deferral edges). Idempotent: if workflows with these names already exist for the tenant, reuse their IDs; only create on cold start. Cache the workflow IDs in a small `engine_workflows` table so subsequent item creations know where to point.

**Rationale:**
Every item the orchestrator creates needs a `workflowId` FK. Bootstrap pins those IDs once.

**Acceptance Criteria:**
- [ ] Lifespan hook in `src/app/lifespan.py` calls `ensure_workflows(settings, client, db)` on startup.
- [ ] New table `engine_workflows` (`name text PK, engine_workflow_id uuid`) via Alembic migration.
- [ ] Idempotency: if the engine returns `409 name exists`, fetch the existing workflow via `GET /api/workflows?name=...` and record its id locally.
- [ ] Bootstrap is a no-op if local cache already has both entries.
- [ ] Unit test with `respx` covers: cold-start create × 2; restart no-op; 409-then-fetch path.
- [ ] `uv run pyright`, `ruff`, tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/lifecycle/bootstrap.py` — new.
- `src/app/lifespan.py` — wire into startup.
- `src/app/modules/ai/models.py` — add `EngineWorkflow`.
- Alembic migration.
- `tests/modules/ai/lifecycle/test_bootstrap.py` — new.

**Technical Notes:**
State lists + transitions are declared in Python code, not YAML, for now. If future projects need their own lifecycles this moves to a per-project config. Keep the declarations next to the bootstrap helper.

---

## Schema rework

### T-130: Engine webhook ingress `/hooks/engine/lifecycle/item-transitioned`

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-128

**Description:**
New endpoint receives engine-emitted webhook events when an item transitions. Verifies the signature (engine's existing HMAC, same secret as FEAT-002's `/hooks/engine/events`), persists a `WebhookEvent(source='engine', event_type='lifecycle_item_transitioned')`, then dispatches to a reactor that:

- Looks up the orchestrator's `work_items` / `tasks` row by `engine_item_id`.
- Fires the derivation appropriate to the transition (W2 when a task moves `proposed → approved`; W5 when the last non-terminal task becomes terminal; T4 when a task enters `approved`).
- Writes the `Approval` / `TaskAssignment` / `TaskPlan` / `TaskImplementation` rows reactively (the data the signal endpoint handed us, threaded through the transition's `correlationId`).

**Rationale:**
Moving derivations + audit writes into the webhook path is what actually pays for the engine integration — other tools that subscribe to the same webhook see the same events in the same order.

**Acceptance Criteria:**
- [ ] `POST /hooks/engine/lifecycle/item-transitioned` live, persist-before-react pipeline.
- [ ] Bad-signature events persisted with `signature_ok=false`, `401` returned.
- [ ] Reactor maps engine item IDs to orchestrator rows and fires the right derivation.
- [ ] Reactor is idempotent: same `(engine_item_id, from_status, to_status, transitioned_at)` replayed is a no-op (engine can re-send on retry).
- [ ] Route tests with fixtures cover W2, W5, T4 derivations + idempotent replay.
- [ ] `uv run pyright`, `ruff`, tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/router.py` — new route.
- `src/app/modules/ai/lifecycle/reactor.py` — new.
- `tests/modules/ai/lifecycle/test_reactor.py` — new.

**Technical Notes:**
The engine webhook delivers `fromStatus` + `toStatus` + `itemId`. Use those to dispatch: a `task_workflow` transition with `toStatus=approved` triggers T4 (advance to `assigning`); with `toStatus=done`, triggers W5 on the parent work item. Keep the dispatch logic centralized here so other derivations added later have one home.

---

### T-131: Reduce `work_items` + `tasks` tables, add `engine_item_id` FK

**Type:** Database
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-129

**Description:**
Alembic migration removes `status`, `locked_from` from `work_items` and `status`, `deferred_from` from `tasks`. Adds `engine_item_id uuid NOT NULL UNIQUE` to both pointing at engine's item-id. Check constraints for the enum values are dropped along with the columns. Existing `WorkItemStatus` / `TaskStatus` enums remain in Python for DTO-layer use (reading cached engine state — see T-132).

**Rationale:**
Engine is now the source of truth for state. Keeping stale columns locally invites drift.

**Acceptance Criteria:**
- [ ] Single migration drops the columns and adds `engine_item_id`.
- [ ] Migration round-trips on local Postgres.
- [ ] SQLAlchemy models updated. Read helpers return the DTO with state pulled from a cache or via a fresh engine query (T-132 decides).
- [ ] Existing test fixtures seeding `status=` directly are updated to omit it.
- [ ] `uv run pyright`, `ruff`, model tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/models.py` — update.
- Alembic migration.
- `tests/modules/ai/test_models.py` — update.

**Technical Notes:**
Two alternatives for state reads: (1) pull from engine every time (simple, chatty); (2) cache in a new `status_cached` column kept fresh by the reactor (fast, requires eventual consistency with engine). Start with (1) for simplicity; add the cache only if a profiler finds the N+1 problem.

---

## Service rewire

### T-132: Rewire lifecycle/work_items.py + lifecycle/tasks.py to engine

**Type:** Backend
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-128, T-131

**Description:**
Replace the direct SQL UPDATE inside every transition function with an engine call:

```python
await engine_client.transition_item(
    item_id=wi.engine_item_id,
    to_status="in_progress",
    correlation_id=<idempotency_key_or_request_id>,
)
```

The engine returns the new state; the orchestrator no longer hand-validates "is this edge allowed?" (the engine does that). The ConflictError that routes currently raise is now wrapped around an engine `422` response ("Transition from X to Y is not allowed"). `_load_locked` / `SELECT ... FOR UPDATE` is removed — state no longer lives locally.

Derivations and `Approval` writes move out of these functions and into T-130's reactor.

**Rationale:**
This is the core of the realignment. Without this, engine integration is decorative.

**Acceptance Criteria:**
- [ ] All 8 work-item + 12 task transition functions rewired.
- [ ] Engine `422` → `ConflictError` with the engine's message preserved.
- [ ] Engine `404` → `NotFoundError`.
- [ ] No local SQL UPDATE of `status` / `locked_from` / `deferred_from` (those columns no longer exist per T-131).
- [ ] `approve_task` no longer calls `maybe_advance_to_in_progress` inline — the reactor does.
- [ ] Rejection paths (T3, T8, T11) keep recording the `Approval` row at signal time (the engine has no concept of rejection-with-feedback; the audit lives here).
- [ ] Lifecycle unit tests updated to assert against mocked engine interactions.
- [ ] `uv run pyright`, `ruff`, tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/lifecycle/work_items.py` — substantial refactor.
- `src/app/modules/ai/lifecycle/tasks.py` — substantial refactor.
- `tests/modules/ai/lifecycle/test_work_items.py` — update.
- `tests/modules/ai/lifecycle/test_tasks.py` — update.

**Technical Notes:**
Rejection edges are interesting. On T3 (`reject_task_proposal`) the engine sees no transition — the task's engine state stays `proposed`. The `Approval` row goes through its own path, and the engine never knows about the rejection iteration count. That's fine — approval history is a richer orchestrator concern, not shared flow state.

---

### T-133: Signal-endpoint adapters: wire engine client + correlation IDs

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-132

**Description:**
The 14 signal endpoints' request/response shapes don't change. What changes is what their service adapters do internally: they now compute the idempotency key, call the rewired transition function (which hits the engine), and return. Reactive work (W2/W5/T4, Approval row writes, TaskAssignment row writes) happens later when the engine's webhook arrives at T-130's reactor.

Passing context from request to reactor: include a `correlation_id` on each engine call — a UUID the orchestrator generates + stores in a small `pending_signal_context` table keyed by `correlation_id`. The reactor looks up this row when the webhook arrives and knows what auxiliary data to write. Row is deleted after the reactor consumes it.

**Rationale:**
Without context passing, the reactor can't know things like "this transition was caused by a `reject-plan` signal with feedback='...'" → can't write the `Approval` row correctly.

**Acceptance Criteria:**
- [ ] `pending_signal_context` table: `correlation_id uuid PK, signal_name text, payload jsonb, created_at timestamptz`.
- [ ] Each of the 14 service adapters inserts a row, calls the engine with the same correlation_id, returns.
- [ ] Reactor (T-130) looks up the row on webhook arrival, writes the Approval / TaskAssignment / TaskPlan / TaskImplementation based on signal_name + payload, deletes the row.
- [ ] Route tests run against mocked engine + synthetic webhook delivery to assert the reactor wrote the right auxiliary rows.
- [ ] `uv run pyright`, `ruff`, tests green.

**Files to Modify/Create:**
- `src/app/modules/ai/lifecycle/service.py` — every adapter updated.
- `src/app/modules/ai/models.py` — `PendingSignalContext`.
- Alembic migration.
- `tests/modules/ai/test_router_*.py` — update to inject mocked engine + drive webhook.

**Technical Notes:**
If the engine transition call succeeds but the webhook doesn't arrive within N seconds, something's wrong. Add a background task (or a CLI command) that scans `pending_signal_context` for stale rows and logs a warning. Out of scope for the main task; file as a follow-up.

---

## Tests + docs

### T-134: Test suite update + E2E rework

**Type:** Testing
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-133

**Description:**
Every test that currently drives the state machine end-to-end needs updating:

- Unit tests in `tests/modules/ai/lifecycle/test_*.py` — swap "assert row.status == X" for "assert engine.transition_item was called with to_status=X".
- Route tests in `tests/modules/ai/test_router_*.py` — inject a mocked `FlowEngineLifecycleClient` via dependency override; drive the webhook separately via an in-test helper.
- `tests/integration/test_feat006_e2e.py` — the big E2E. Either (a) run against a real flow engine via docker-compose (preferred for AC-2 integration coverage), or (b) mock it at the client boundary. Start with (b); add (a) as a future `tests/integration/test_feat006_e2e_real_engine.py`.

**Rationale:**
rc1 tests assumed state in the orchestrator's own DB. None of them compile after T-131/T-132.

**Acceptance Criteria:**
- [ ] All existing FEAT-006 tests pass against the engine-backed implementation.
- [ ] New integration test variant against a real flow-engine instance (docker-compose + env override) — may be marked `@pytest.mark.requires_engine` and opt-in.
- [ ] No test writes directly to `work_items.status` or `tasks.status` (those columns no longer exist).
- [ ] `uv run pytest tests/modules tests/integration` fully green.

**Files to Modify/Create:**
- All `tests/modules/ai/lifecycle/test_*.py` — update.
- All `tests/modules/ai/test_router_*.py` — update.
- `tests/integration/test_feat006_e2e.py` — update.
- `tests/integration/test_feat006_e2e_real_engine.py` — new (opt-in).

**Technical Notes:**
The opt-in real-engine test is the first integration we have across the two repos. Document how to run it in the README: `docker compose -f docker-compose.yml -f <engine compose> up -d && uv run pytest -m requires_engine`.

---

### T-135: Docs + FEAT-006 brief update + rc2 changelog

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-134

**Description:**
Reconcile all docs with the engine-backed implementation:

- `docs/data-model.md` — drop `status` / `locked_from` / `deferred_from` entries, add `engine_item_id`, add `PendingSignalContext` + `EngineWorkflow` entities, add changelog entry.
- `docs/api-spec.md` — add `/hooks/engine/lifecycle/item-transitioned` webhook, document auth.
- `docs/ARCHITECTURE.md` — new subsection "Deterministic flow state in the engine"; update data-flow diagrams.
- `docs/work-items/FEAT-006-deterministic-lifecycle-flow.md` — flip Status to "Delivered — v0.6.0-rc2", update the Delivery Notes section explaining the rc1 → rc2 rework, refresh AC status (AC-9 formally ✅ — engine is now demonstrably in the loop).
- `CLAUDE.md` — update directory list with new submodules; add a pattern note: "Lifecycle state transitions go through the engine; the orchestrator caches reads via the reactor."

**Acceptance Criteria:**
- [ ] All four doc files reflect rc2.
- [ ] FEAT-006 brief acknowledges the rc1 → rc2 delta explicitly.
- [ ] Changelog entries on `data-model.md` + `api-spec.md` + `ARCHITECTURE.md`.
- [ ] Post-generation checklist from `.ai-framework/prompts/feature-tasks.md` passes.

**Files to Modify/Create:**
- All of the above.

**Technical Notes:**
Be surgical. Don't re-architect the docs — just reconcile the delta.

---

## Summary

**Totals by type:** Backend 5 (T-128, T-129, T-130, T-132, T-133) · Database 1 (T-131) · Testing 1 (T-134) · Documentation 1 (T-135).

**Complexity:** S 2 (T-129, T-135) · M 4 (T-128, T-130, T-131, T-133) · L 2 (T-132, T-134).

**Critical path (depth 7):**
`T-128 → T-129 → T-131 → T-132 → T-133 → T-134 → T-135`.

**Risks / open questions:**

- **Engine JWT lifecycle.** If the tenant's API key needs rotation, the orchestrator picks it up only on restart. Operationally fine for v1; document in `CLAUDE.md`.
- **Webhook delivery latency.** Engine webhook ingress → reactor → Approval write is eventually-consistent. Client-side, a signal returning `202` no longer guarantees the Approval row exists yet. Test assertions that wait on the row need a poll-with-timeout helper.
- **Real-engine test parity.** The opt-in integration test needs both repos running. If the engine's Transitions API shape drifts, orchestrator breaks silently. Add a contract test pinned to the engine's OpenAPI schema in a follow-up.
- **Rejection audit inside engine.** Rejections don't produce engine transitions — the `Approval` row is the only record. Cross-tool views querying the engine won't see rejection iteration counts. Document as a known v1 trade-off; reconsider if reviewer dashboards demand the engine be aware.
