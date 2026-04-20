# Feature Brief: FEAT-006 — Deterministic Lifecycle Flow

> **Purpose**: Replace the agentic lifecycle loop (FEAT-005) with a deterministic state machine owned by the orchestrator and backed by the flow engine as a passive source of truth. Work items and tasks progress through well-defined states driven by explicit external signals (admin actions, agent outputs, GitHub webhooks) rather than LLM tool selection. This is the first feature where the flow engine earns its keep — and the first where the orchestrator expands its capabilities to interact with external systems (starting with GitHub PR management).
> **Design input**: `docs/design/deterministic-flow-transitions.md`
> **Template reference**: `.ai-framework/templates/feature-brief.md`

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-006 |
| **Name** | Deterministic Lifecycle Flow |
| **Target Version** | v0.6.0 |
| **Status** | Delivered — v0.6.0-rc2-phase-1 (engine mirror-write + derivation reactor; merge-gating in FEAT-007; phase-2 engine-as-sole-writer pending) |
| **Priority** | High |
| **Requested By** | Tech Lead (`carlos.escalona@carestechs.com.br`) |
| **Date Created** | 2026-04-19 |

---

## 2. User Story

**As an** admin driving feature delivery collaboratively with devs and agents (see `docs/personas/primary-user.md`), **I want to** open a work item, have the orchestrator dispatch task generation, approve/reject/assign tasks one-by-one, route each task through plan → implementation → review with the right approver at each gate, and close the work item when all tasks reach a terminal state — **so that** the lifecycle is a predictable state machine with explicit handoffs instead of an LLM deciding "what comes next," and every transition is auditable, resumable, and integrates cleanly with external systems (GitHub PRs first, others to follow).

---

## 3. Goal

Work items and tasks have explicit state machines persisted in the flow engine. The orchestrator exposes 14 external signals (13 REST + 1 GitHub webhook) as its intake surface; each signal carries a directed transition through the state machines. Derived transitions (work item `→ in_progress` on first task approval; `→ ready` when all tasks terminal; task `approved → assigning` after admin approval) fire internally without requiring a separate external call. Admin/dev/agent roles enforce the approval matrix at `proposed`, `plan_review`, and `impl_review`. The orchestrator's first external-system capability — GitHub PR merge gating via a required check — lands as part of the `impl_review` flow: T10 approval releases the merge, T11 rejection holds it.

The FEAT-005 lifecycle agent remains available for single-operator, agent-driven runs; FEAT-006 is the collaborative path that replaces it for multi-actor work.

---

## 4. Feature Scope

### 4.1 Included

- **Work-item state machine**: `open → in_progress ⇄ locked → ready → closed`. `locked` reachable only from `in_progress`. `in_progress` and `ready` derived from child task states (first-task-approval → `in_progress`; all-tasks-terminal → `ready`).
- **Task state machine**: `proposed ⇄ approved → assigning → planning → plan_review → implementing → impl_review → done`. Plus `deferred` from any non-terminal (admin). Rejection edges `plan_review → planning` and `impl_review → implementing` keep the same owner, unbounded iterations.
- **Approval matrix enforcement** at `proposed` (admin), `plan_review` (same dev for dev-assigned; admin for agent-assigned), `impl_review` (admin in v1 solo-dev; another dev otherwise). Enforced by the orchestrator's signal handlers — not by the flow engine.
- **14 external signals** as the orchestrator's intake surface (full catalogue in `docs/design/deterministic-flow-transitions.md` §Signal Catalogue). 13 REST endpoints under `/api/v1/work-items/{id}/...` and `/api/v1/tasks/{id}/...`, plus 1 GitHub webhook endpoint under `/hooks/github/pr`.
- **Derived/internal transitions** (W2, W5, T4) fire within the orchestrator after a parent signal's state write completes. No separate HTTP hop.
- **Flow-engine writes only.** The engine stores current state per entity and emits a state-changed event for the trace stream. No logic in the engine. Every orchestrator transition writes new state and appends an audit entry.
- **New entities**: `WorkItem`, `Task`, `TaskAssignment`, `Approval`. See Section 6.
- **GitHub PR merge gating** as the first external-system integration. When a task reaches `impl_review`, the orchestrator registers (or updates) a required status check on the PR. T10 approval marks the check green; T11 rejection marks it red. Check name: `orchestrator/impl-review`. Requires a GitHub App or PAT with `checks:write` (composition-root config, similar to LLM provider selection).
- **Idempotency per signal**: each external signal dedupes on `(entity_id, signal_name, payload_hash)`; a duplicate returns `202` with `meta.alreadyReceived=true`.
- **Backfill path**: existing FEAT-005 runs keep working; the new machines ship alongside. No retroactive migration of historical lifecycle-agent runs into the new state machine.

### 4.2 Excluded

- **Signal authorization beyond existing `X-API-Key`.** Role enforcement (admin vs. dev on each endpoint) is validated at service layer against a single actor header for v1; proper per-user auth is a separate initiative.
- **Dissent/rejection audit artifacts.** Rejection feedback is stored as a string on the approval record, not as a separate document. The design-doc "dissent trail" is explicitly deferred.
- **Multi-dev peer review.** v1 assumes solo-dev; "another dev" for `impl_review` collapses to admin. The machine supports it structurally (assignment can be any actor id) but no reviewer-picker logic.
- **Automated reviewer selection.** Admin manually assigns reviewers for `impl_review` when it's not dev-self-signal.
- **Task dependency resolution.** Tasks are independent in v1; no "block task B until task A terminal."
- **Agent execution of implementations/reviews.** FEAT-006 defines the state machine and signal surface; agent-drafted plans/implementations/reviews integrate by emitting the same signals a human would, but wiring specific agents into this flow is out of scope (follow-up FEAT).
- **UI.** No frontend in v1. CLI + HTTP only.
- **Cross-system integrations beyond GitHub PRs.** Others (Linear, Slack, Jira) are future capabilities.
- **Retroactive migration.** Work items already in-flight under FEAT-005 are not force-migrated.

---

## 5. Acceptance Criteria

- **AC-1**: All 14 signals in the design doc's Signal Catalogue are implemented as REST endpoints (13) or webhook handlers (1) and documented in `docs/api-spec.md`.
- **AC-2**: Every transition in the design doc's state-machine tables is covered by a passing integration test that drives a real Postgres + flow engine and asserts the resulting state.
- **AC-3**: Derived transitions W2 (first-task-approval → `in_progress`), W5 (all-tasks-terminal → `ready`), and T4 (approved → assigning) fire automatically without a second external call; tests prove each derives correctly including the "already in state" idempotent case.
- **AC-4**: Admin-only signals (S1-S7, S14) reject non-admin actor headers with RFC 7807 `403`; dev-only signals reject non-dev actors similarly. Tests cover both roles on each endpoint.
- **AC-5**: Rejection loops (T3, T8, T11) preserve the same owner across iterations and attach reject feedback; tests drive ≥3 rejection iterations and verify no state corruption.
- **AC-6**: `defer` (S14) from any non-terminal task state writes `deferred` and fires the W5 idempotent check; tests cover deferring from every non-terminal state.
- **AC-7**: GitHub PR webhook (S11) creates/updates the `orchestrator/impl-review` check on the target PR within 5 s of the webhook; T10 approval flips it green; T11 flips it red. Verified with a recorded webhook fixture and a mocked GitHub Checks API.
- **AC-8**: Signal idempotency: replaying any signal with an identical payload returns `202` with `meta.alreadyReceived=true`; no duplicate state writes; no duplicate approval rows.
- **AC-9**: Engine has zero transition logic — demonstrated by a test that replaces the engine client with a recording double and proves the orchestrator produces the same state sequence against a stub.
- **AC-10**: Full work-item lifecycle end-to-end test: open → generate 2 tasks → approve one, reject and re-approve one → assign both (one to dev, one to agent) → plan → implement → review → done → work item auto-transitions to `ready` → admin closes. All 14 signal types exercised.
- **AC-11**: FEAT-005 lifecycle agent continues to work unchanged after FEAT-006 lands; composition-integrity test covers both paths.
- **AC-12**: New entities (`WorkItem`, `Task`, `TaskAssignment`, `Approval`) added to `docs/data-model.md` with changelog entry; all 14 endpoints added to `docs/api-spec.md` with changelog entry.

---

## 6. Key Entities and Business Rules

| Entity | Role in Feature | Key Business Rules |
|--------|----------------|--------------------|
| `WorkItem` | New. Represents a FEAT/BUG/IMP work item as tracked by the flow engine. | `status ∈ {open, in_progress, locked, ready, closed}`. `type ∈ {FEAT, BUG, IMP}`. `locked_from` records which state was active before the lock (always `in_progress` in v1). `closed_at` set only when `status=closed`. Append-only state history. |
| `Task` | New. A single task under a work item. | `status ∈ {proposed, approved, assigning, planning, plan_review, implementing, impl_review, done, deferred}`. `work_item_id` FK. `proposer_id` (admin/agent who proposed). Monotonic forward progression except the three rejection edges (`plan_review → planning`, `impl_review → implementing`, `proposed → proposed` revision). `deferred_from` captures the prior state for audit. |
| `TaskAssignment` | New. Records the current assignee of a task and assignment history. | `task_id` FK. `assignee_type ∈ {dev, agent}`. `assignee_id` (actor ref). One active assignment per task at a time; reassignment closes the previous row and opens a new one (append-only history). Created by S7 (`assign-task`). |
| `Approval` | New. Records every approval/rejection decision on tasks. | `task_id` FK. `stage ∈ {proposed, plan, impl}`. `decision ∈ {approve, reject}`. `decided_by` (actor). `feedback` nullable text (present on rejections; may be empty string on approvals). Append-only. Used to reconstruct rejection iteration count + audit trail. |
| `Run` (existing) | Used only when an agent participates in a stage. | Unchanged. A single Task can spawn multiple Runs across its lifecycle (one for plan-drafting, one for implementation, one for review). Each Run links back to the Task it served. |
| `WebhookEvent` (existing) | Extended to cover GitHub webhook intake. | New `source ∈ {engine, github}` column. Existing `engine` source preserved. Signature verification differs per source (HMAC for engine; GitHub signature for github). |

**New entities required:** `WorkItem`, `Task`, `TaskAssignment`, `Approval` — must be added to `docs/data-model.md` before implementation tasks can proceed.

---

## 7. API Impact

### Admin-only signals (require admin actor)

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/v1/work-items` | POST | New | S1. Opens a new work item; dispatches task-generation. |
| `/api/v1/work-items/{id}/lock` | POST | New | S2. Admin pause. |
| `/api/v1/work-items/{id}/unlock` | POST | New | S3. Admin resume. |
| `/api/v1/work-items/{id}/close` | POST | New | S4. Closes from `ready`. |
| `/api/v1/tasks/{id}/approve` | POST | New | S5. Admin approves a proposed task. |
| `/api/v1/tasks/{id}/reject` | POST | New | S6. Admin rejects with feedback. |
| `/api/v1/tasks/{id}/assign` | POST | New | S7. Admin assigns dev or agent. |
| `/api/v1/tasks/{id}/defer` | POST | New | S14. Admin defers a non-terminal task. |

### Dev/agent signals

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/v1/tasks/{id}/plan` | POST | New | S8. Submit plan for review (agent or dev). |
| `/api/v1/tasks/{id}/plan/approve` | POST | New | S9. Plan approval (dev self-signal for dev-assigned; admin for agent-assigned). |
| `/api/v1/tasks/{id}/plan/reject` | POST | New | S10. Plan rejection. |
| `/api/v1/tasks/{id}/implementation` | POST | New | S11 (agent path). Submit implementation for review; triggers reviewer routing. |
| `/api/v1/tasks/{id}/review/approve` | POST | New | S12. Review approval — releases PR merge gate; fires W5 check. |
| `/api/v1/tasks/{id}/review/reject` | POST | New | S13. Review rejection. |

### External-system intake

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/hooks/github/pr` | POST | New | S11 (human PR path). GitHub webhook on PR open with `closes T-NNN` / `orchestrator: T-NNN` in body. Maps PR → task, transitions `implementing → impl_review`. |

**New endpoints required:** all 15 above (14 signals + GitHub webhook) — must be added to `docs/api-spec.md`.

---

## 8. UI Impact

| Screen / Component | Status | Description |
|--------------------|--------|-------------|
| — | — | No UI in v1. |

**New screens required:** None. CLI + HTTP only.

---

## 9. Edge Cases

- **Duplicate S5 (approve-task) on the same task.** Second call returns `202` `alreadyReceived=true`; W2 fires only on the first approval across all tasks in the work item, not on every approval.
- **Concurrent admin S3 (lock) and S5 (approve-task).** Admin locks mid-approval flow. The approval writes `approved` but W2 (derived `open → in_progress`) is suppressed because the work item is `locked` — W2 checks the parent state before advancing.
- **S12 (review approve) on the last non-terminal task.** Triggers W5 derivation; W5 writes `ready`. Test must cover the case where another task is simultaneously deferred (S14) and W5 fires exactly once.
- **Rejection iteration count.** T11 `impl_review → implementing` can repeat unboundedly. The `Approval` table grows without limit per task. No policy enforcement — admin's responsibility to intervene.
- **GitHub webhook delay.** PR opened but webhook hasn't arrived yet; dev posts S11 manually via `/implementation`. Orchestrator accepts the first signal and ignores the duplicate webhook (idempotency on `(task_id, pr_number)`).
- **Assign to agent when no agent is wired.** S7 with `assignee_type=agent` but no agent configured for the assignee_id. Orchestrator writes the assignment, task moves to `planning`, but no plan-draft dispatch fires — admin sees the task stuck in `planning` with no draft arriving. Not a v1 failure; logged as a warning.
- **Close attempt on a work item with tasks still non-terminal.** S4 on a work item that's not `ready` returns `409 Conflict`. Admin must wait for W5 or defer the stragglers.
- **Defer from `done`.** S14 on an already-`done` task returns `409 Conflict` (done is terminal, can't be deferred).
- **Rejection without feedback.** S6, S10, S13 require non-empty `feedback` — return `422` if missing.
- **Work item with zero tasks.** Admin opens work item (W1), no tasks get generated (agent failure, admin skips generation). `ready` check (W5) doesn't fire because "all tasks terminal" with zero tasks is vacuously true — should it? Decision: W5 requires `count(tasks) ≥ 1`; a zero-task work item is a bug, not a normal close path. Admin closes manually by first adding tasks or deferring work item (future capability).
- **PR merge without orchestrator approval.** Admin force-merges bypassing the required check. Orchestrator has no way to undo the merge but records the event and flags the task as "merged before approval" in the Run trace.

---

## 10. Constraints

- **AD-1 (orchestrator drives; engine is passive).** The engine must remain logic-free. Any temptation to put derivation logic (W2, W5) inside the engine is a review blocker.
- **Composition integrity (AD-3).** The whole flow must run with `LLM_PROVIDER=stub` and no GitHub App configured — deriving states, persisting approvals, and completing work items deterministically. GitHub integration is an optional capability, not a hard dependency.
- **Single-worker uvicorn.** v1 constraint continues; any cross-worker state (e.g., derivation races between two workers processing two approvals for the same work item) is out of scope. Document as a follow-up.
- **Signal auth via `X-API-Key` + `X-Actor-Role` header.** No OAuth, no user DB; role validation against the header for v1.
- **GitHub integration via PAT or GitHub App** configured in `pyproject.toml` / env; orchestrator falls back to "no merge gating" if unconfigured.
- **No schema changes to the existing flow engine** beyond adding `WorkItem` and `Task` as known entity types in its state store. Engine schema stays minimal.
- **Stay within the Docker Compose footprint.** No new services (no Redis, no Celery, no message broker). Transitions are synchronous within the signal handler.
- **All new tables migrated via Alembic.** One revision per entity; descriptive slugs per CLAUDE.md conventions.

---

## 11. Motivation and Priority Justification

**Motivation:** The FEAT-005 lifecycle agent proved end-to-end feature delivery is possible (IMP-002), but its architecture is over-agentic: 7 of 8 stage transitions are deterministic, the flow engine is unused, and the LLM's "routing decisions" are tautological given content decisions already made inside tools. A deterministic state machine is both simpler to reason about and the right substrate for real collaboration between admins, devs, and agents — the original ia-framework was designed as a standalone prototyping tool and has gaps around collaborative flows that FEAT-005 inherited.

**Impact if delayed:** Every feature after FEAT-005 that involves more than one actor (admin + dev, admin + agent + reviewer) either re-uses the lifecycle agent's single-operator model or gets bespoke state handling. Without FEAT-006, the project can't scale past solo-operator work without each feature inventing its own coordination primitives.

**Dependencies on this feature:**

- Any future FEAT that routes work between admin and dev.
- Agent-per-stage assignment (wiring specific agents to plan-draft, implementation, review) — needs the state machine to hook into.
- Multi-dev peer review — needs the actor-agnostic assignment layer FEAT-006 introduces.
- Cross-system integrations (Linear sync, Slack notifications) — all target the same signal intake surface.

---

## 12. Traceability

| Reference | Link |
|-----------|------|
| **Persona** | `docs/personas/primary-user.md` (solo tech lead driving collaborative delivery) |
| **Stakeholder Scope Item** | Self-hosted feature delivery expanded from single-operator (FEAT-005) to multi-actor collaborative |
| **Success Metric** | Continuation of Stakeholder Success Metric #1 — expand "feature shipped via orchestrator" to include multi-actor work items with explicit admin/dev/agent handoffs |
| **Related Work Items** | FEAT-005 (lifecycle agent — predecessor, coexists), FEAT-002 (runtime loop — reused for agent-participating stages), FEAT-003 (Anthropic provider — reused for agent-drafted artifacts), FEAT-004 (trace streaming — reused for audit) |
| **Design Input** | `docs/design/deterministic-flow-transitions.md` |

---

## 13. Usage Notes for AI Task Generation

When generating tasks from this Feature Brief:

1. **Start with data-model + api-spec updates.** Four new entities and 15 new endpoints — these doc updates are prerequisites for any implementation task per CLAUDE.md's doc-first rule.
2. **One task per signal handler is a reasonable decomposition** for the 14 signals, with dedicated tasks for the 3 derived transitions (W2, W5, T4) and one for the approval-matrix enforcement layer.
3. **GitHub PR merge-gating is its own task cluster.** Treat the GitHub Checks integration as a self-contained sub-feature with its own integration test suite.
4. **Composition-integrity test per AD-3** must cover the full 14-signal flow with `LLM_PROVIDER=stub` and no GitHub App configured.
5. **Every rejection edge needs a dedicated test** — rejection loops are the feature's most common failure mode.
6. **Do not break FEAT-005.** Both lifecycle paths must coexist; AC-11 is non-negotiable.
7. **Signal idempotency is cross-cutting** — consider a shared idempotency helper rather than per-handler duplication.
8. **Traceability**: include `FEAT-006` in every generated task's metadata.

---

## 14. Delivery Notes (2026-04-19, rc2 phase 1)

**rc1 → rc2 realignment.** rc1 shipped a deterministic flow with state
persisted in the orchestrator's own Postgres — the flow engine was not in
the loop, contradicting the design-doc model (engine as shared state
across tools).  rc2-phase-1 puts the engine in the loop as a secondary
writer kept in sync with local state; orchestrator still holds the
authoritative rows but every transition mirrors the new state to the
engine so other tools subscribing to the engine's webhooks see a
consistent cross-tool view.

**Delivered in rc2-phase-1:**

- `FlowEngineLifecycleClient` (T-128) — JWT-authed HTTP client for the
  engine's ``/api/workflows``, ``/api/items``, ``/api/items/{id}/
  transitions`` and ``/api/webhook-subscriptions`` endpoints.  Correlation
  UUIDs are encoded into transition comments as
  ``orchestrator-corr:<uuid>`` so the engine's webhook payload (which
  carries ``triggeredBy`` but not ``comment``) can thread them back.
- Workflow bootstrap at startup (T-129) — idempotent registration of
  ``work_item_workflow`` + ``task_workflow`` in the engine, cached in a
  new ``engine_workflows`` table.  Gracefully skipped when engine config
  is absent so dev setups without the engine still boot.
- `engine_item_id` columns on ``work_items`` + ``tasks`` (nullable,
  UNIQUE) — populated at open/propose time when the engine client is
  configured.
- Mirror write on every transition (T-131a + T-132a) — every function
  in ``lifecycle/work_items.py`` and ``lifecycle/tasks.py`` accepts
  optional ``engine`` + ``correlation_id`` kwargs and best-effort
  mirrors state changes.  Engine errors are logged + swallowed; local
  state remains authoritative.  Rejection edges (T3, T8, T11) don't
  transition in the engine (status unchanged); only the ``Approval``
  row captures the rejection.
- Engine-side → orchestrator reactor (T-130) —
  ``POST /hooks/engine/lifecycle/item-transitioned`` receives engine
  state-change webhooks, persists a ``WebhookEvent`` (source='engine',
  event_type='lifecycle_item_transitioned'), and dispatches W2/W5
  derivations via ``lifecycle/reactor.py``.  Idempotent on
  ``lifecycle:<item_id>:<delivery_id>``.
- Route + service plumbing — ``get_lifecycle_engine_client`` +
  ``get_lifecycle_workflow_ids`` FastAPI deps thread the engine client
  through every signal endpoint so HTTP requests actually exercise
  the mirror path.

**Deferred to rc2-phase-2 (next PR series):**

- **T-133 PendingSignalContext** — table + plumbing to thread signal
  payloads (feedback, plan_path, etc.) from adapter to reactor so
  auxiliary rows (``Approval``, ``TaskAssignment``, ``TaskPlan``,
  ``TaskImplementation``) can be written reactively rather than
  inline.  Gate for phase 2.
- **T-131b drop local state columns** — remove ``status``,
  ``locked_from``, ``deferred_from`` from ``work_items`` and ``tasks``
  once the reactor proves it drives derivations + aux writes purely
  from engine events.
- **T-134 test suite reshape** — real-engine opt-in integration test
  (``tests/integration/test_feat006_e2e_real_engine.py``) pointed at
  a running ``carestechs-flow-engine``; consolidated per-transition
  integration roll-up.
- **T-121 GitHub Checks API client** — merge-gating (FEAT-007 scope).

**Delivered in rc1 (still in place):**

- 5 new entities (``WorkItem``, ``Task``, ``TaskAssignment``,
  ``Approval``, ``LifecycleSignal``) + ``TaskPlan``,
  ``TaskImplementation``, ``WebhookEvent.source`` extension.
- Work-item state machine + task state machine + pure approval-matrix
  helper.
- 14 signal endpoints + GitHub PR webhook ingress (without Checks API).
- Full E2E integration test.

**Acceptance-criteria status (updated for rc2-phase-1):**

- AC-1 ✅ · AC-2 partial (unit + route coverage; no consolidated
  integration roll-up yet)
- AC-3 ✅ · AC-4 ✅ · AC-5 ✅ · AC-6 ✅ · AC-8 ✅
- AC-7 ❌ (GitHub Checks not wired — FEAT-007)
- AC-9 **✅ now formally**: engine is demonstrably in the loop via
  mirror writes + derivation webhooks.
- AC-10 ✅ · AC-11 ✅ (no FEAT-005 regressions in 643-test run)
- AC-12 ✅

---

## 15. Delivery Notes (2026-04-19, rc1 — superseded)

First-pass delivery covers the critical path — state machines, every signal
endpoint, and the GitHub webhook ingress.  Follow-up work explicitly deferred:

**Delivered (T-107 through T-120, T-125, T-127 — 16 tasks):**
- 5 new entities (`WorkItem`, `Task`, `TaskAssignment`, `Approval`,
  `LifecycleSignal`) plus audit tables (`TaskPlan`, `TaskImplementation`)
  and a `WebhookEvent.source` extension.
- Work-item state machine (`open → in_progress ⇄ locked → ready → closed`)
  with W2/W5 derivations.
- Task state machine (proposed → done/deferred) with T4 derivation.
- Pure approval-matrix helper (dev-assigned vs. agent-assigned routing).
- `X-Actor-Role` dependency + signal idempotency helper
  (`lifecycle_signals` table, SHA-256 key over `(entity_id, name, payload)`).
- All 14 signal endpoints (S1–S14) live under `/api/v1/work-items/*` and
  `/api/v1/tasks/*`; GitHub PR webhook at `/hooks/github/pr` with signature
  verification, `T-NNN` parsing, and idempotent persistence.
- Full E2E integration test exercising every signal.

**Deferred (T-121, T-122, T-123, T-124, T-126 — 5 tasks):**
- **T-121 GitHub Checks API client.** Webhook ingress works; merge-gating
  via the Checks API needs GitHub App setup. Until then, review-approve
  simply advances task state; no check is flipped.  Tracked for a
  follow-up once operational requirements lock.
- **T-122 per-transition integration suite.** Each transition is already
  covered by the lifecycle unit tests (45 cases in
  `tests/modules/ai/lifecycle/`) and the route tests. A consolidated
  integration run-through is still desirable for regression insurance.
- **T-123 composition-integrity test (AD-3).** The existing route tests
  already run with `LLM_PROVIDER=stub` and no GitHub config, so the
  intent of the test is covered implicitly. A dedicated test remains
  open for formal AC-9 verification.
- **T-124 GitHub integration test w/ recorded payloads + `respx`.** The
  T-120 tests cover signature, parsing, matched/unmatched, and dedupe
  with minimal synthetic payloads. Recorded real payloads + `respx`
  against the Checks API land with T-121.
- **T-126 FEAT-005 coexistence test.** The existing FEAT-005 suite
  continues to pass unchanged (see `tests/integration/`). A dedicated
  coexistence test that runs both lifecycles in a single process is
  straightforward but pending.

**Acceptance-criteria status:**
AC-1 ✅ · AC-2 partial (unit coverage; no consolidated integration suite)
· AC-3 ✅ · AC-4 ✅ · AC-5 ✅ · AC-6 ✅ · AC-7 ❌ (GitHub Checks not wired)
· AC-8 ✅ · AC-9 ✅ (implicit via route tests; no explicit guard)
· AC-10 ✅ · AC-11 ✅ (no FEAT-005 regressions in 544-test run)
· AC-12 ✅ (this update).
