# Feature Brief: FEAT-011 — Deterministic lifecycle agent port (`lifecycle-agent@0.3.0`)

> **Purpose**: Re-express the production lifecycle as a `flow.policy: deterministic` agent on the FEAT-009 executor seam, retiring the LLM-as-policy path for this workload. The eight v0.1.0 tools become nodes with explicit transitions, branch predicates, and registered executors — engine-bound nodes use FEAT-010's `EngineExecutor`; LLM-using steps (brief synthesis, task planning, prompt generation) become *executors that internally call an LLM* — not LLM-as-policy. Pause-for-implementation becomes a `HumanExecutor`. The result is: orchestrator orchestrates, executors execute, LLM is a tool inside specific executors that need it.
>
> **Relationship to FEAT-009.** FEAT-009 stood up the seam and shipped a 2-node demo. This FEAT proves the seam against the real production workload — every edge case the v0.1.0 lifecycle handles (correction budget, rejection paths, pause-for-signal, idempotency, aux-row materialization) must work under deterministic flow.
>
> **Relationship to FEAT-010.** FEAT-010 ships the `EngineExecutor` and the dispatch-wake reactor extension. This FEAT consumes them — every node that today calls `FlowEngineLifecycleClient` inline becomes an `EngineExecutor` registration in v0.3.0.
>
> **Relationship to FEAT-012.** This FEAT will surface aux-write path requirements (Approval, TaskAssignment, TaskPlan, TaskImplementation) when LLM-content executors complete; FEAT-012 lands those flows uniformly through the outbox. Sequence: A → B → C, with B and C overlapping on the closing PR.
> **Template reference**: `.ai-framework/templates/feature-brief.md`

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-011 |
| **Name** | Deterministic lifecycle agent port (`lifecycle-agent@0.3.0`) |
| **Target Version** | v0.6.0 |
| **Status** | Not Started |
| **Priority** | High |
| **Requested By** | Project owner (FEAT-009 closing review — v0.1.0 LLM-policy path is the only one driving real engine work; v0.2.0 demo doesn't prove the seam against production load) |
| **Date Created** | 2026-04-26 |

---

## 2. User Story

**As an** orchestrator operator running the lifecycle agent in production, **I want** the lifecycle to run on `flow.policy: deterministic` with engine transitions dispatched through registered executors, **so that** node selection is a pure function of YAML + memory + dispatch results — no LLM tax on flow decisions, every artifact-producing step is a uniform dispatch, and the operational surface (pause/resume, correction budget, rejection paths, restart safety) matches v0.1.0 bit-for-bit.

---

## 3. Goal

A `lifecycle-agent@0.3.0` YAML agent declaring `flow.policy: deterministic` reaches closure on a real work item end-to-end, driving engine state through `EngineExecutor` registrations and producing artefacts (briefs, plans, implementations, reviews) through LLM-internal executors — with no LLM call in the runtime loop itself. The v0.1.0 agent and its LLM-policy code path keep working unchanged; v0.3.0 is the recommended path going forward, v0.1.0 is "preserved for migration window, scheduled for removal."

---

## 4. Feature Scope

### 4.1 Included

- **`agents/lifecycle-agent@0.3.0.yaml`** — full deterministic re-expression of the v0.1.0 lifecycle. Eight nodes (`load_work_item`, `synthesize_brief`, `generate_tasks`, `generate_plan`, `request_implementation`, `review_implementation`, `correct_implementation`, `close_work_item`) with declared transitions, terminal nodes, intake schema, and branch predicates for every multi-target transition. No `policy.systemPrompts` block at the agent level — system prompts move into per-node executor configuration.
- **Branch predicates** registered in `flow_predicates.py`:
  - `unplanned_tasks_remaining` (already exists from FEAT-009) — drives `generate_plan` self-loop.
  - `correction_attempts_under_bound` (already exists) — drives `correct_implementation` → `request_implementation` vs terminal.
  - **New**: `review_passed` (`result.verdict == "pass"`) — drives `review_implementation → close_work_item` vs `correct_implementation`.
  - **New**: `task_rejected` (`result.outcome == "rejected"`) — drives task-flow rejection branches.
- **Engine-bound executors** registered in `executors/bootstrap.py::_register_lifecycle_v03`: every node that today calls `FlowEngineLifecycleClient` inline gets an `EngineExecutor(transition_key=...)` registration. Mapping table is part of this FEAT's design output (work-item W1–W6 + task T1–T12 → node names).
- **LLM-content executors** — new `LLMContentExecutor` adapter under `executors/llm_content.py` that wraps a `core.llm` provider call as a dispatch. Constructor takes `system_prompt`, `user_prompt_template`, `result_schema`, and the model client. On dispatch, renders prompts from `DispatchContext`, calls the LLM, validates the result against `result_schema`, returns the dispatch envelope. This is where today's `policy.systemPrompts[node]` content lands. The LLM is *inside* the executor, not at the runtime layer.
- **Pause-for-implementation as `HumanExecutor`** — `request_implementation` registers a `HumanExecutor` that returns `dispatched` immediately and waits for `POST /api/v1/runs/{id}/signals` with `name=implementation-complete` (existing v0.1.0 contract). Per-task idempotency on `(run_id, name, task_id)` preserved.
- **`LifecycleMemory` migration decision and execution.** Two viable shapes:
  1. *Keep `LifecycleMemory` as a typed schema* persisted in `RunMemory.data` under a stable namespace; executors read/write it via helpers.
  2. *Fold into RunMemory generic dict* with executors threading state through `__memory_patch` returns (the FEAT-009 v0.2.0 pattern).
  This FEAT picks one and implements it; the choice is part of the design output, not deferred. Shape (1) is the safer port; shape (2) is the cleaner long-term shape but requires more migration churn.
- **Correction budget enforcement** in deterministic flow. Today the budget is checked inside `runtime.py`'s LLM-policy stop-condition pipeline. Under deterministic flow, the bound is a *branch predicate* (`correction_attempts_under_bound`) plus a stop-condition entry: when the predicate goes false, the resolver routes to a terminal node with `final_state.reason=correction_budget_exceeded` and the runtime maps to `RunStatus.FAILED`.
- **Rejection paths.** Task-rejection transitions (T3/T8/T11) preserve their FEAT-008 contract: aux row written, no engine call. Under deterministic flow, the rejection node's executor is a local executor that writes the `Approval` row and emits `__memory_patch`; the resolver routes to the next node based on `result.outcome`.
- **End-to-end integration test** (`test_lifecycle_v03_end_to_end`) that loads `lifecycle-agent@0.3.0`, runs against a `respx`-stubbed engine + `StubLLMProvider` for the LLM-content executors, asserts: every v0.1.0 e2e behavior (correction budget trip, rejection path, pause/resume, restart safety) reproduces under deterministic flow.
- **Migration documentation:** `docs/migration/lifecycle-v01-to-v03.md` covering: how to run a single work item through v0.3.0, what changes operationally (none externally — same `/api/v1/runs` surface), what to verify before flipping the default agent ref.
- **Doc sweep:** `CLAUDE.md` directory map updated; v0.3.0 added to agents inventory; FEAT-005 lifecycle agent doc updated with a "v0.3.0 supersedes v0.1.0" note (v0.1.0 not yet deleted).

### 4.2 Excluded

- **Deleting `lifecycle-agent@0.1.0` or the LLM-policy runtime.** v0.1.0 stays; deletion is a separate future FEAT once v0.3.0 has soaked in production for a defined window.
- **Schema evolution of work-item / task tables.** Aux writes use existing FEAT-008 columns; no new fields.
- **New engine workflows.** v0.3.0 maps onto the existing FEAT-006 work-item and task workflows. If a real branch can't be expressed against existing transitions, that's a scope violation — escalate to a new FEAT before extending.
- **Multi-agent orchestration.** Only the lifecycle workload is ported. Generalizing the `LLMContentExecutor` pattern to a registry-of-prompts is a future FEAT once a second consumer demands it.
- **Live LLM in CI.** Integration tests use `StubLLMProvider`. The `tests/contract/` live-LLM smoke is preserved as opt-in.
- **A new operator UI.** Headless service.

---

## 5. Acceptance Criteria

- **AC-1**: `lifecycle-agent@0.3.0` reaches `RunStatus.COMPLETED` end-to-end on a sample work item using `respx`-stubbed engine + `StubLLMProvider`, with no `core.llm` import in `runtime_deterministic.py` (FEAT-009 structural guard still holds).
- **AC-2**: The correction-budget edge case (3rd correction attempt) terminates the run with `final_state.reason=correction_budget_exceeded` and `RunStatus.FAILED` — same contract as v0.1.0. Verified by parametrized test exercising attempts 1, 2, 3.
- **AC-3**: A task rejection (`result.outcome == "rejected"`) at any approval stage routes to the rejection branch, writes the `Approval` aux row, and does not call the engine — matches v0.1.0 / FEAT-008 contract.
- **AC-4**: Pause-for-implementation: `request_implementation` dispatch suspends the run; `POST /api/v1/runs/{id}/signals` with `name=implementation-complete` resumes it. Idempotency on `(run_id, name, task_id)` preserved. Verified by integration test that signals before *and* after the pause.
- **AC-5**: Restart safety: a run interrupted mid-engine-dispatch resumes correctly via `reconcile-dispatches` (FEAT-010) + `reconcile-aux` (FEAT-008). No aux row dropped, no double-transition.
- **AC-6**: Coverage validation refuses to boot if any v0.3.0 node lacks an executor registration *or* explicit exemption. Bootstrap log includes the v0.3.0 binding count.
- **AC-7**: The v0.1.0 LLM-policy end-to-end test suite continues to pass unchanged. v0.3.0 is purely additive.
- **AC-8**: Branch resolution is exhaustive. A unit test walks every transition in v0.3.0 — including `review_implementation` (pass/fail), `generate_plan` (self-loop / done), `correct_implementation` (under-budget / exceeded), and every task-flow rejection — without instantiating any LLM client.
- **AC-9**: A live-LLM contract test (`@pytest.mark.live`, off by default) drives v0.3.0 end-to-end against the real Anthropic provider, producing a real brief + real plan + real review for a throwaway work item. Demonstrates the LLM-content executor path works against the real provider, not just the stub.

---

## 6. Key Entities and Business Rules

| Entity | Role in Feature | Key Business Rules |
|--------|----------------|--------------------|
| `Run` | Created via `POST /api/v1/runs` with `agent_ref=lifecycle-agent@0.3.0` | `intake.workItemPath` required; routes through `runtime_deterministic.py` |
| `RunMemory` | Holds `LifecycleMemory` projection (or replaces it — design decision in FEAT scope) | Per-run; never shared; threaded via `__memory_patch` envelope |
| `Dispatch` | One per node execution; engine-bound dispatches use `mode=engine` (FEAT-010) | State machine FEAT-009 invariants apply; correlation id required for engine mode |
| `PendingAuxWrite` | Written by engine-bound dispatches; consumed by reactor (FEAT-008) | Idempotent on correlation id |
| `Approval` / `TaskAssignment` / `TaskPlan` / `TaskImplementation` | Materialized by reactor on `item.transitioned` webhook | Reactor is sole writer under engine-present mode; rejection paths write inline |
| `RunSignal` | Pause/resume primitive for `HumanExecutor` (request_implementation) | Idempotent on `(run_id, name, task_id)` |
| `WorkItem` / `Task` | Authoritative state owned by engine; status columns reactor-managed cache | No inline writes from v0.3.0 executors |

**New entities required:** None. All persistence reused from FEAT-005/006/008/009.

---

## 7. API Impact

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/v1/runs` | POST | Existing | Accept `agent_ref=lifecycle-agent@0.3.0` (added to allowlist if one exists); body shape unchanged |
| `/api/v1/runs/{id}/signals` | POST | Existing | Resumes pause-for-implementation in v0.3.0 same as v0.1.0 |
| `/hooks/lifecycle/transitions` | POST | Existing | Reactor extension from FEAT-010 wakes v0.3.0 dispatches; payload unchanged |

**New endpoints required:** None.

---

## 8. UI Impact

N/A.

---

## 9. Edge Cases

- **Branch expression can't capture a v0.1.0 LLM judgment.** If during port a branch is found that genuinely needs LLM judgment (e.g. "is this brief well-formed enough to proceed?"), the design has two outs: (a) widen the dispatch result envelope so the predicate has data to resolve on, or (b) leave that *one* node behind a `flow.policy: llm` sub-flow — but document why before reaching for it. The default answer is (a).
- **Mid-run upgrade from v0.1.0 to v0.3.0.** Out of scope. A run pinned to `lifecycle-agent@0.1.0` runs to completion under that agent; new runs use v0.3.0. No live migration of in-flight runs.
- **`LifecycleMemory` schema drift.** If shape (1) is chosen and the typed schema evolves, migrations follow the existing Pydantic-versioned-memory pattern. If shape (2) is chosen, in-flight runs that started under v0.1.0 finish under v0.1.0 — no schema bridging needed.
- **Engine cache miss / 404 on workflow registration.** BUG-002 fix already covers tenant-scoped cache and stale-cache 404 recovery; v0.3.0 inherits.
- **LLM-content executor: hallucinated output that fails `result_schema`.** Treated as dispatch failure; runtime advances to error state. Bounded retry (small N) configurable per node — *not* unbounded.
- **Concurrent signal + webhook arrival.** Existing FEAT-006 ordering (persist → reconcile → wake) preserved; supervisor handles either order.
- **Run cancelled mid-dispatch.** Existing FEAT-009 cancel-propagation through `_purge_signals_for_run` extended to also resolve open dispatch futures with `DispatchCancelled`.

---

## 10. Constraints

- v0.1.0 e2e suite must remain green throughout the FEAT — this is the regression bar. Any v0.1.0 test failure during v0.3.0 work is a stop-and-fix.
- Must not modify `core.llm`. The `LLMContentExecutor` is built on top.
- Must not modify `flow_resolver.py` or `runtime_deterministic.py`. If the resolver's branch syntax is insufficient for a transition, that's an escalation, not an inline patch.
- Must not extend the engine's workflow definitions without a separate FEAT.
- Single-worker constraint preserved; supervisor stays process-local.
- Correction budget remains configurable via `LIFECYCLE_MAX_CORRECTIONS` env var; default unchanged.

---

## 11. Motivation and Priority Justification

**Motivation:** FEAT-009 proved the seam composes; FEAT-010 makes engine dispatch fit the seam. But until the production lifecycle runs on `flow.policy: deterministic`, v0.1.0 LLM-policy is the *only* path validated end-to-end against a real engine. The architecture thesis stays unproven. Worse, every operational change (correction budget tuning, new approval stage, additional review step) has to land in two places — the LLM-policy stop-conditions layer and any future deterministic agent — until v0.3.0 lands and v0.1.0 enters retirement.

**Impact if delayed:** v0.1.0 stays the production path indefinitely. The LLM-as-policy code path can't be retired. Pure-orchestrator architecture remains aspirational, not load-bearing.

**Dependencies on this feature:** FEAT-012 (aux writes from executors) consumes v0.3.0's dispatch shapes when designing the unified outbox path. The "remove v0.1.0 + LLM-policy runtime" follow-on FEAT is fully blocked on this.

---

## 12. Traceability

| Reference | Link |
|-----------|------|
| **Persona** | Orchestrator operator |
| **Stakeholder Scope Item** | Headless agent loop drives `carestechs-flow-engine` over HTTP; orchestrator self-delivery (AD-6) |
| **Success Metric** | Lifecycle runs reach closure without manual intervention; LLM cost per run reduced (no per-iteration policy call) |
| **Related Work Items** | FEAT-005 (v0.1.0 lifecycle agent), FEAT-006 (deterministic flow), FEAT-008 (engine as authority), FEAT-009 (orchestrator as pure orchestrator), FEAT-010 (engine executor), FEAT-012 (next) |

---

## 13. Usage Notes for AI Task Generation

1. **Port, don't redesign.** v0.3.0 must reproduce v0.1.0 behavior on every observable axis — same final states, same aux rows, same idempotency. Behavioral drift = bug.
2. **Design output before code:** before generating implementation tasks, the FEAT requires (a) a node-to-engine-transition mapping table, (b) a `LifecycleMemory` shape decision (1 or 2), and (c) a branch-predicate inventory. These are work-product, not background.
3. **Acceptance criteria 1, 7, and 8 are non-negotiable.** v0.1.0 green + structural guard intact + exhaustive branch walk are the minimum bar.
4. **Live-LLM contract test (AC-9) is a separate task** with its own `@pytest.mark.live` guard. Don't fold into the main e2e.
5. **Migration doc is part of the FEAT, not a follow-on.** AC-7 + the migration doc together define "v0.3.0 is operationally equivalent."
6. **Reach for `flow.policy: llm` only if the resolver expression syntax demonstrably can't capture the decision** — and document why in the migration doc, not just the code.
