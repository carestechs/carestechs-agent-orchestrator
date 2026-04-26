# FEAT-011 — Deterministic lifecycle agent port (`lifecycle-agent@0.3.0`)

> **Source:** `docs/work-items/FEAT-011-lifecycle-agent-deterministic-port.md`
> **Status:** Not Started
> **Target version:** v0.6.0

FEAT-011 re-expresses the production lifecycle (`lifecycle-agent@0.1.0`, FEAT-005) as a `flow.policy: deterministic` agent on top of FEAT-009's executor seam and FEAT-010's `EngineExecutor`. The eight v0.1.0 tools become nodes with declared transitions and registered executors: engine-bound transitions (work-item W1–W6, task T1–T12) go through `EngineExecutor`; LLM-content steps (brief synthesis, task planning, plan generation, review) go through a new `LLMContentExecutor` that wraps `core.llm` as a dispatch; pause-for-implementation goes through a `HumanExecutor`. The runtime loop never sees an LLM call. v0.1.0 stays in place unchanged as the regression bar.

The numbering picks up at **T-250** (FEAT-010 used T-230..T-240; T-241..T-249 are reserved so FEAT-010's docs/operational sweep can grow without collision).

---

## Foundation

### T-250: Design doc — deterministic lifecycle port (mapping table + memory shape + predicates)

**Type:** Documentation
**Workflow:** standard
**Complexity:** M
**Dependencies:** None

**Description:**
Write `docs/design/feat-011-lifecycle-deterministic-port.md` as the architectural decision behind FEAT-011. Three load-bearing outputs the brief (Section 13) names as work-product, not background:

1. **Node-to-engine-transition mapping table.** Every v0.3.0 node lists: which executor mode it registers (`engine` / `local` LLM-content / `human`), and — for engine-bound nodes — which `transition_key` (e.g. `work_item.W2`, `task.T6`) it binds. Cross-reference `modules/ai/lifecycle/declarations.py`. Engine-bound: at minimum `load_work_item` (W1), `generate_tasks` (W3), `assign_task` (T1), `generate_plan` (T2), `review_implementation` pass-branch (T7/T9 etc.), `correct_implementation` (T-correction), `close_work_item` (W4/W5/W6 sequence). Resolve the exact set in this doc — do not defer to T-253.
2. **`LifecycleMemory` shape decision.** Choose **one** explicitly:
   - **Shape (1)**: keep the typed `LifecycleMemory` schema (Pydantic v2 model) persisted in `RunMemory.data` under a stable namespace; executors read/write via helpers in `tools/lifecycle/memory.py`.
   - **Shape (2)**: fold into `RunMemory` generic dict, executors thread state via `__memory_patch` envelope returns (FEAT-009 v0.2.0 pattern).
   The doc must recommend ONE with rationale. Tasks T-254..T-261 implement the chosen shape — there is no "decide later" branch.
3. **Branch-predicate inventory.** Reused: `unplanned_tasks_remaining`, `correction_attempts_under_bound`. New: `review_passed` (`result.verdict == "pass"`), `task_rejected` (`result.outcome == "rejected"`). Doc lists every multi-target transition in v0.3.0 and the predicate driving it.

Cross-link from `CLAUDE.md` Patterns ("LLM-content nodes register an `LLMContentExecutor`; the runtime loop never imports `core.llm`") and from `docs/design/feat-009-pure-orchestrator.md` ("FEAT-011 ports the production lifecycle onto this seam").

**Rationale:**
AC-1, AC-8, brief Section 13. Without these three artefacts pinned in writing, T-253 (the YAML port) becomes a guessing exercise and reviewers relitigate the memory-shape question on every PR. The mapping table is also the input that downstream FEAT-012 will read when designing the unified aux-write outbox path.

**Acceptance Criteria:**
- [ ] Design doc names the principle: orchestrator orchestrates, executors execute, the LLM is a tool inside specific executors that need it.
- [ ] Mapping table covers all eight v0.3.0 nodes; every engine-bound node names its `transition_key` (or explicitly notes `local` / `human` mode).
- [ ] `LifecycleMemory` shape decision is **single-valued** (1 *or* 2) with at least three sentences of rationale and a note on what migrates if the choice flips.
- [ ] Branch-predicate inventory enumerates every multi-target transition; new predicates `review_passed` and `task_rejected` are spec'd by name and `result.<field>` shape.
- [ ] Cross-linked from `CLAUDE.md` and from `docs/design/feat-009-pure-orchestrator.md` + `docs/design/feat-010-engine-executor.md`.
- [ ] Mentions the AC-7 regression bar: every PR in this FEAT runs the v0.1.0 e2e suite as a non-negotiable gate.

**Files to Modify/Create:**
- `docs/design/feat-011-lifecycle-deterministic-port.md` — new.
- `CLAUDE.md` — Patterns entry + cross-link.
- `docs/design/feat-009-pure-orchestrator.md` — forward-link banner.
- `docs/design/feat-010-engine-executor.md` — forward-link to FEAT-011 as the first real consumer.

---

### T-251: Branch-predicate registry — `review_passed` and `task_rejected`

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-250

**Description:**
Extend `src/app/modules/ai/flow_predicates.py` with two new predicates:

- `review_passed`: returns `True` when `result.verdict == "pass"` on the upstream dispatch envelope; `False` on `"fail"`; raises `PredicateError` on any other value.
- `task_rejected`: returns `True` when `result.outcome == "rejected"`; `False` on `"approved"`; raises `PredicateError` otherwise.

Register both in the predicate registry consumed by `flow_resolver.py`. **`flow_resolver.py` is not modified** — this is a pure registry extension, per brief Section 10 constraint.

**Rationale:**
AC-1, AC-3, AC-8. The two new predicates close the gap between the v0.1.0 LLM-judgment pattern (the model picks the next node) and v0.3.0's pure-function resolution (the YAML branch maps a typed `result` field to the next node). Without them, `review_implementation` and the rejection-bearing approval transitions can't express their branches in YAML.

**Acceptance Criteria:**
- [ ] `flow_predicates.py` exports the two new predicates and registers them in the predicate registry.
- [ ] `flow_resolver.py` is **not modified** (asserted by `git diff` in PR description).
- [ ] Unit tests: each predicate exercises pass / fail / raise paths against a synthetic `DispatchEnvelope`.
- [ ] Each predicate carries a docstring naming the envelope field it reads and the producer node that supplies it.

**Files to Modify/Create:**
- `src/app/modules/ai/flow_predicates.py` — add predicates + registry entries.
- `tests/modules/ai/test_flow_predicates_lifecycle.py` — new.

**Technical Notes:**
The brief explicitly leaves the resolver-expression syntax (`result.<field> <op> <literal>`) on the table as an alternative to a named predicate. If the YAML port (T-253) can express both branches with the inline expression form, T-251 collapses to "no new named predicates needed; resolver expression form covers both" and the file change shrinks to a unit test only. The design doc (T-250) makes the call.

---

## Backend — agent + executors

### T-252: `LLMContentExecutor` — wrap a `core.llm` call as a dispatch

**Type:** Backend
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-250

**Description:**
Introduce `src/app/modules/ai/executors/llm_content.py` — `LLMContentExecutor` implementing the FEAT-009 `Executor` Protocol. Constructor parameters: `ref: str`, `system_prompt: str`, `user_prompt_template: str`, `result_schema: type[BaseModel]`, `llm_provider: LLMProvider`, optional `max_retries: int = 1`, optional `model: str | None = None`. `mode: ClassVar[ExecutorMode] = "local"` (the LLM call is in-process; engine mode is reserved for `EngineExecutor`).

Dispatch behavior: render `system_prompt` and `user_prompt_template` against `DispatchContext` (intake + memory snapshot); call `llm_provider.complete(...)`; validate the model output against `result_schema` (Pydantic v2 model); on schema-validation failure, retry up to `max_retries` then return a `failed` envelope with `outcome="error"` and `detail="result_schema_validation_failed"`; on success return a `dispatched`-then-`completed` envelope (sync local-mode dispatch — no wake required) with `result` carrying the validated payload as `dict`.

**`llm_provider` is constructor-injected** — `executors/llm_content.py` does **not** import any provider SDK at module scope. Only `from app.core.llm import LLMProvider` (the abstraction) at module scope; any concrete provider (Anthropic / Stub) is supplied by the bootstrap helper. Verified by T-262.

**Rationale:**
AC-1, AC-9. This is the seam that lets v0.3.0 keep doing real LLM-driven content production (briefs, plans, reviews) without putting an LLM call in the runtime loop. The executor is to FEAT-011 what `EngineExecutor` was to FEAT-010 — a fourth shape on the seam, not a fork in the loop.

**Acceptance Criteria:**
- [ ] `executors/llm_content.py` exists; class `LLMContentExecutor` with the constructor signature above; `mode: ClassVar[ExecutorMode] = "local"`.
- [ ] Dispatch renders prompts from `DispatchContext` (intake + memory) — no hidden global state read.
- [ ] Schema validation success → `completed` envelope with validated `result` dict.
- [ ] Schema validation failure (after retries exhausted) → `failed` envelope with `outcome="error"` and `detail="result_schema_validation_failed"`; `corrections` are bounded (configurable, default 1) — *not* unbounded retries.
- [ ] Provider transient failure (timeout / 5xx) — defers to the `LLMProvider`'s own retry policy; no second retry wrapper here.
- [ ] No module-scope import of `anthropic` (or any provider SDK); only `from app.core.llm import LLMProvider`. Asserted by T-262.
- [ ] Unit tests: success path with `StubLLMProvider`, schema-validation failure path with retry exhaustion, prompt-rendering of intake + memory fields, missing-template-variable raises a clear error before the LLM call.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/llm_content.py` — new.
- `tests/modules/ai/executors/test_llm_content_executor.py` — new.

**Technical Notes:**
Prompt rendering uses `str.format_map(...)` against a flat dict assembled from `DispatchContext` (intake + memory) — keep it boring. If a prompt needs richer templating (Jinja, partials), defer that to a future FEAT and use the boring path now. `result_schema` is a Pydantic v2 model — runtime-loadable, not a JSONSchema dict; this matches how `core.llm` providers already validate tool inputs.

---

### T-253: `agents/lifecycle-agent@0.3.0.yaml` — full deterministic port

**Type:** Backend
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-250, T-251

**Description:**
Author `agents/lifecycle-agent@0.3.0.yaml` declaring `flow.policy: deterministic` and re-expressing the v0.1.0 lifecycle as eight nodes with declared transitions. Every multi-target transition carries a `branch:` block (named predicate from T-251 or `result.<field> <op> <literal>` expression). **No `policy.systemPrompts` block at the agent level** — system prompts are an executor configuration (consumed by `LLMContentExecutor` constructor in T-254), not an agent-level concern.

Nodes (final names confirmed in T-250's mapping table; provisional list):

- `load_work_item` — engine-bound (W1: create → in_progress mirror) + LLM-content for brief synthesis.
- `generate_tasks` — LLM-content (writes `tasks/<id>-tasks.md`); engine transition for W3 if mapping table requires.
- `assign_task` — engine-bound (T1: assign).
- `generate_plan` — LLM-content (writes `plans/plan-<task_id>-<slug>.md`); self-loop until `unplanned_tasks_remaining` flips false.
- `request_implementation` — `human` mode (pause-for-signal; replaces v0.1.0 `wait_for_implementation`).
- `review_implementation` — LLM-content (judges pass/fail); branches on `review_passed`.
- `correct_implementation` — local executor that increments `correction_attempts[task_id]` + writes the `Approval` row inline (rejection contract preserved); branches on `correction_attempts_under_bound`.
- `close_work_item` — engine-bound terminal (W4/W5/W6 sequence).

Transitions, terminal nodes, and `intakeSchema` mirror v0.1.0 except: the `corrections` v0.1.0 node is renamed `correct_implementation` for verb-clarity (FEAT-009 convention); `wait_for_implementation` is renamed `request_implementation` for the same reason. Final renames confirmed in T-250.

**Rationale:**
AC-1, AC-7, AC-8. The YAML is the contract between the resolver, the executor registry, and the runtime loop. Every later test in this FEAT runs against this file.

**Acceptance Criteria:**
- [ ] `agents/lifecycle-agent@0.3.0.yaml` exists; `flow.policy: deterministic`.
- [ ] All multi-target transitions declare a `branch:` block; `flow_resolver.py` resolves every branch without instantiating any LLM client (asserted by T-263).
- [ ] No `policy.systemPrompts` key at agent level (system prompts live with their executor in T-254).
- [ ] `intakeSchema` accepts `workItemPath` (same as v0.1.0).
- [ ] Terminal nodes set; `defaultBudget.maxSteps` set conservatively (≥300, mirroring v0.1.0).
- [ ] Agent loader accepts the YAML at lifespan startup (no schema rejection).
- [ ] `lifecycle-agent@0.1.0.yaml` and `@0.2.0.yaml` are **not modified** (asserted by `git diff`).

**Files to Modify/Create:**
- `agents/lifecycle-agent@0.3.0.yaml` — new.

**Technical Notes:**
If during port a branch is found that genuinely needs LLM judgment (per brief Section 9 edge case), the design has two outs: (a) widen the dispatch result envelope so the predicate has data to resolve on, or (b) leave that one node behind a `flow.policy: llm` sub-flow. Default answer is (a). Document the call in `docs/migration/lifecycle-v01-to-v03.md` (T-269), not in code.

---

### T-254: `register_lifecycle_v03` bootstrap — wire every v0.3.0 node to its executor

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-252, T-253

**Description:**
Add `register_lifecycle_v03(registry, *, lifecycle_client, llm_provider, session_factory, max_corrections, ...)` in `src/app/modules/ai/executors/bootstrap.py`. For each v0.3.0 node:

- **Engine-bound nodes** → `register_engine_executor(...)` (FEAT-010 helper) with the node's `transition_key` from T-250's mapping table.
- **LLM-content nodes** → instantiate `LLMContentExecutor(system_prompt=..., user_prompt_template=..., result_schema=..., llm_provider=llm_provider)` and register against `(agent_ref, node_name)`.
- **`request_implementation`** → register a `HumanExecutor` (FEAT-009) keyed on signal name `implementation-complete` with `task_id` carried in the dispatch result envelope.
- **`correct_implementation`** → register a `LocalExecutor` whose callable writes the `Approval` row inline (FEAT-008 rejection contract) + emits `__memory_patch` incrementing `correction_attempts[task_id]`.

Wire the helper into `register_all_executors` so lifespan startup binds every v0.3.0 node automatically when the agent is loaded. Coverage validator (FEAT-009) refuses to boot if any node is left unbound — verified by T-265.

System prompts for LLM-content executors live as text files under `src/app/modules/ai/executors/prompts/lifecycle/` (one file per node), loaded at bootstrap time. Same content as v0.1.0's `policy.systemPrompts` references in `.ai-framework/prompts/` — copied, not symlinked, so v0.1.0 retirement doesn't pull them out from under v0.3.0.

**Rationale:**
AC-1, AC-6. Bootstrap is the single source of truth for executor wiring; agents declare nodes, bootstrap binds executors. Mirrors `register_engine_executor` (FEAT-010) and `LocalExecutor` registration patterns.

**Acceptance Criteria:**
- [ ] `register_lifecycle_v03` exists; takes registry + collaborators (`lifecycle_client`, `llm_provider`, `session_factory`, `max_corrections`).
- [ ] Every v0.3.0 node registered exactly once; duplicate registration raises `ExecutorRegistryError` per the existing registry contract.
- [ ] `register_all_executors` calls `register_lifecycle_v03` when the v0.3.0 YAML is loaded; skipped when not loaded (no boot failure if v0.3.0 isn't on disk).
- [ ] Engine-absent dev mode: if `lifecycle_client` is `None`, the helper raises `RuntimeError` naming the first engine-bound node — not a silent fallback (per FEAT-010 T-232 contract).
- [ ] System-prompt text files exist under `src/app/modules/ai/executors/prompts/lifecycle/`; bootstrap loads them at registration time and the executor caches them.
- [ ] Unit test exercises the helper end-to-end against a stub registry + stub clients; asserts every v0.3.0 node ends up bound.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/bootstrap.py` — `register_lifecycle_v03` + wiring into `register_all_executors`.
- `src/app/modules/ai/executors/prompts/lifecycle/` — new directory; one `.md` per LLM-content node (`load_work_item.md`, `generate_tasks.md`, `generate_plan.md`, `review_implementation.md`).
- `tests/modules/ai/executors/test_bootstrap_lifecycle_v03.py` — new.

---

## Backend — semantics

### T-255: `LifecycleMemory` shape implementation per T-250 decision

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-250, T-254

**Description:**
Implement the `LifecycleMemory` shape chosen in T-250's design doc.

- **If shape (1) (typed schema persisted under namespace)**: keep `tools/lifecycle/memory.py`'s typed schema; add helpers `read_lifecycle_memory(run_memory) -> LifecycleMemory` and `write_lifecycle_memory(run_memory, model)` that serialize/deserialize under a stable `lifecycle.v1` namespace in `RunMemory.data`. Every v0.3.0 executor reads/writes through these helpers; no executor touches `RunMemory.data` directly.
- **If shape (2) (generic dict + `__memory_patch`)**: deprecate `LifecycleMemory` typed schema as an internal-only convenience; every v0.3.0 executor returns `__memory_patch` envelopes the runtime applies to `RunMemory.data` (FEAT-009 v0.2.0 pattern). Document the field-name conventions in T-269's migration doc.

Either way: v0.1.0 continues to use the existing `LifecycleMemory` path unchanged; the shape decision affects v0.3.0 only.

**Rationale:**
AC-1, AC-7. The lifecycle agent's per-run state has nontrivial structure (work item, tasks list, correction counters, current task pointer); the shape decision is load-bearing because every executor implementation in T-254 depends on it.

**Acceptance Criteria:**
- [ ] Shape (1) or shape (2) implementation matches the design doc exactly.
- [ ] All v0.3.0 executors read/write memory through the chosen path.
- [ ] No v0.3.0 executor reads `RunMemory.data` directly (shape 1) or via a helper (shape 2 — `__memory_patch` envelopes only).
- [ ] v0.1.0 memory path unchanged (asserted by passing the existing v0.1.0 e2e suite, T-267).
- [ ] Unit tests cover the chosen shape's round-trip + patch-merge semantics.

**Files to Modify/Create:**
- `src/app/modules/ai/tools/lifecycle/memory.py` — adjust per shape.
- `tests/modules/ai/tools/lifecycle/test_memory_shape.py` — new.

---

### T-256: Pause-for-implementation as `HumanExecutor` — preserve idempotency contract

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-254

**Description:**
The v0.3.0 `request_implementation` node registers a `HumanExecutor` (FEAT-009) keyed on signal name `implementation-complete`. Dispatch returns `dispatched` immediately; the runtime suspends; `POST /api/v1/runs/{id}/signals` resumes via the existing supervisor wake path.

Preserve the v0.1.0 idempotency contract: signals are idempotent on `(run_id, name, task_id)` — a duplicate POST returns `202` with `meta.alreadyReceived=true` and does not double-deliver to the supervisor. `task_id` is carried in the signal payload and matched against the dispatch's intake. If `HumanExecutor` (FEAT-009) does not yet support a per-payload-key idempotency tuple, extend it minimally — do not fork a new executor.

**Rationale:**
AC-4. The pause/resume contract is the most operator-facing behavior in v0.1.0; bit-for-bit preservation is the bar.

**Acceptance Criteria:**
- [ ] `request_implementation` dispatch suspends the run; supervisor records the awaited signal name + task id.
- [ ] `POST /api/v1/runs/{id}/signals` with `name=implementation-complete` + `task_id=<id>` resumes the run.
- [ ] Duplicate POST with same `(run_id, name, task_id)` returns `202` + `meta.alreadyReceived=true`; the dispatch is woken **once**.
- [ ] Signal arriving **before** the dispatch (race) is buffered and consumed when `request_implementation` next dispatches for that `task_id` (existing v0.1.0 behavior — verified by integration test in T-260).
- [ ] If `HumanExecutor` is extended, the extension is reviewed against the FEAT-009 design doc; FEAT-009's structural import-quarantine test still passes.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/human.py` — minimal extension if needed for `(run_id, name, task_id)` keying.
- `src/app/modules/ai/lifecycle/service.py` — signal handler keys on `(run_id, name, task_id)` (likely already does; confirm + tests).
- `tests/modules/ai/test_request_implementation_pause.py` — new.

---

### T-257: Correction budget as branch predicate + stop-condition (deterministic flow)

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-251, T-255

**Description:**
Under v0.1.0, the correction budget is enforced inside `runtime.py`'s LLM-policy stop-condition pipeline (`stop_conditions.correction_budget_exceeded`). Under v0.3.0 deterministic flow, the bound is a **branch predicate** + a stop-condition entry:

- `correct_implementation` node's outbound transition declares `branch: correction_attempts_under_bound` → `request_implementation` on `True`, → terminal-failure node (e.g. `terminate_correction_budget`) on `False`.
- A new terminal node `terminate_correction_budget` (or equivalent — name confirmed in T-250) carries `final_state.reason=correction_budget_exceeded`. The runtime's existing `error` priority bucket maps this to `RunStatus.FAILED`.

Read `LIFECYCLE_MAX_CORRECTIONS` (env var, default `2`, unchanged from v0.1.0) at bootstrap and pass into `register_lifecycle_v03` so the predicate has a value to compare against.

**`runtime_deterministic.py` is not modified** — the stop-condition pipeline already maps `final_state.reason` to `RunStatus`. This task is YAML + bootstrap + predicate wiring.

**Rationale:**
AC-2. The correction budget is the most-tested edge case in v0.1.0; v0.3.0 must trip it identically (parametrized over attempts 1, 2, 3 — T-260).

**Acceptance Criteria:**
- [ ] `LIFECYCLE_MAX_CORRECTIONS` read at bootstrap (default `2`) and passed into `register_lifecycle_v03`.
- [ ] `correction_attempts_under_bound` predicate compares `memory.correction_attempts[task_id]` against the bound — predicate already exists from FEAT-009; this task confirms the wiring and adds a unit test against the new YAML's `result.<field>` shape.
- [ ] `terminate_correction_budget` (or equivalent) terminal node exists in v0.3.0 YAML; emits `final_state.reason=correction_budget_exceeded`.
- [ ] Run terminates with `stop_reason=error` and `RunStatus.FAILED` when bound exceeded.
- [ ] `runtime_deterministic.py` is **not modified** (asserted by `git diff`).
- [ ] Unit tests on the predicate + a focused integration test in T-260.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/bootstrap.py` — read env var, plumb into `register_lifecycle_v03`.
- `agents/lifecycle-agent@0.3.0.yaml` — add `terminate_correction_budget` terminal node (confirmed in T-253 and T-250).
- `tests/modules/ai/test_correction_budget_predicate.py` — new.

---

### T-258: Rejection paths — local executor writes Approval inline; resolver routes on `task_rejected`

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-251, T-254

**Description:**
Task-rejection transitions (T3/T8/T11 in `lifecycle/declarations.py`) preserve their FEAT-008 contract: the `Approval` row is written inline, the engine is **not** called. Under v0.3.0:

- The rejection-bearing approval nodes (e.g. `correct_implementation` and any approval-stage node added in T-253) register a `LocalExecutor` whose callable writes the `Approval` row directly (FEAT-008 rejection contract) and returns a dispatch envelope with `result.outcome="rejected"` (or `"approved"`).
- The resolver routes via `task_rejected` predicate (T-251) — `True` to a rejection-handling node, `False` to the normal-flow successor.
- **No `EngineExecutor` registration on rejection branches** — engine state is not advanced on rejection per FEAT-008.

Preserve idempotency on `(task_id, stage)`: a second rejection for the same `(task_id, stage)` is a no-op.

**Rationale:**
AC-3. Rejection paths are the only place in the lifecycle where aux rows are written inline (FEAT-008 contract); v0.3.0 must preserve this surface bit-for-bit. FEAT-012 will fold rejection paths into the unified outbox; v0.3.0 stays on the inline contract.

**Acceptance Criteria:**
- [ ] Rejection-bearing nodes register a `LocalExecutor` (not `EngineExecutor`).
- [ ] On `result.outcome="rejected"`: `Approval` row is written, no engine call is made (asserted by `respx` mock — zero requests to engine).
- [ ] Resolver routes via `task_rejected` to the rejection-handling node.
- [ ] Idempotent on `(task_id, stage)`.
- [ ] Unit tests + integration test in T-261.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/bootstrap.py` — register the rejection `LocalExecutor`.
- `src/app/modules/ai/lifecycle/service.py` — minor: confirm `Approval`-write path is reachable from a `LocalExecutor` callable (likely is; small refactor if not).
- `tests/modules/ai/test_rejection_path_local.py` — new.

---

### T-259: Restart safety — verify reconcile-dispatches + reconcile-aux against v0.3.0

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-254

**Description:**
v0.3.0 inherits FEAT-010's `reconcile-dispatches` (engine-mode dispatches) and FEAT-008's `reconcile-aux` (orphan aux outbox rows). This task adds **no new reconciler logic** — it confirms both reconcilers handle a v0.3.0 run interrupted mid-engine-dispatch correctly, and adds a focused integration test in T-262.

If the reconcilers behave correctly (no aux drop, no double-transition, no orphan dispatch leak), this task closes as a doc + test task. If a v0.3.0-specific gap surfaces (e.g. `LLMContentExecutor` dispatch interrupted mid-LLM-call doesn't reconcile because it's local-mode without an outbox row), the task expands into a small fix scoped here — flag in implementation plan on pickup.

**Rationale:**
AC-5. Restart safety is the operational floor; v0.3.0 must match v0.1.0 + FEAT-010 here.

**Acceptance Criteria:**
- [ ] No new reconciler module; existing `reconcile-dispatches` (FEAT-010) and `reconcile-aux` (FEAT-008) handle v0.3.0 runs.
- [ ] Documentation note in `docs/design/feat-011-lifecycle-deterministic-port.md` (T-250) on which reconciler covers which dispatch mode.
- [ ] If a gap is found, implementation plan on pickup names it explicitly; otherwise this is verification work.

**Files to Modify/Create:**
- `docs/design/feat-011-lifecycle-deterministic-port.md` — note (likely a small append).
- (Possibly) `src/app/modules/ai/executors/reconcile.py` — only if a v0.3.0-specific gap surfaces.

---

## Testing

### T-260: AC-1 + AC-2 e2e — happy path + correction-budget parametrized

**Type:** Testing
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-254, T-255, T-256, T-257

**Description:**
Integration test `tests/integration/test_lifecycle_v03_end_to_end.py`. Loads `lifecycle-agent@0.3.0`, runs against a `respx`-stubbed engine + `StubLLMProvider`. Two test cases:

1. **AC-1 happy path**: run reaches `RunStatus.COMPLETED`; engine receives the expected transition POSTs in order (`work_item.W1`, ..., `work_item.W6`); aux rows materialize; trace shows `mode=local` for LLM-content nodes, `mode=engine` for engine-bound nodes, `mode=local` for `request_implementation` *only on resume* (the dispatch itself is `mode=human` while suspended).
2. **AC-2 correction budget**: parametrized over `attempts ∈ {1, 2, 3}`. With `LIFECYCLE_MAX_CORRECTIONS=2`, attempts 1 and 2 route back to `request_implementation`; attempt 3 trips the predicate and routes to the terminal-failure node, ending in `RunStatus.FAILED` with `final_state.reason=correction_budget_exceeded`.

`StubLLMProvider` is scripted to return well-formed `result_schema` payloads for each LLM-content node. The HMAC signature on inbound webhooks is computed via the production helper (FEAT-008 pattern).

**Rationale:**
AC-1, AC-2. The happy path proves the seam works for the production workload; the budget parametrization proves the deterministic predicate matches v0.1.0 stop-condition semantics.

**Acceptance Criteria:**
- [ ] Test in `tests/integration/test_lifecycle_v03_end_to_end.py`.
- [ ] AC-1 happy path: run reaches `RunStatus.COMPLETED`; engine transition order matches the mapping table from T-250.
- [ ] AC-2 parametrized over attempts 1, 2, 3; attempt 3 produces `RunStatus.FAILED` + `correction_budget_exceeded`.
- [ ] HMAC signature on inbound webhooks via production helper.
- [ ] No `core.llm` import in `runtime_deterministic.py` after the test runs (asserted by T-263).

**Files to Modify/Create:**
- `tests/integration/test_lifecycle_v03_end_to_end.py` — new.
- `tests/fixtures/lifecycle/` — scripted `StubLLMProvider` payloads keyed by node name.

---

### T-261: AC-3 e2e — task rejection routes to rejection branch + writes Approval + no engine call

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-258

**Description:**
Integration test in the same file as T-260 (or sibling): exercise the rejection branch at every approval stage in v0.3.0. For each stage, script `StubLLMProvider` to return `result.outcome="rejected"`; assert:

1. Run routes to the rejection-handling node via the `task_rejected` predicate.
2. An `Approval` row is written for the rejection (visible in the database).
3. The engine receives **zero** transition POSTs for the rejection itself (`respx` assertion: zero requests to the rejection-bearing transition keys T3/T8/T11).
4. Idempotent: a second rejection POST for the same `(task_id, stage)` is a no-op.

**Rationale:**
AC-3. Rejection contract is the only inline-write surface left after FEAT-008; v0.3.0 must preserve it bit-for-bit.

**Acceptance Criteria:**
- [ ] Test exercises every approval stage's rejection branch.
- [ ] `Approval` row is written; engine receives zero transition POSTs for the rejection.
- [ ] Idempotency on `(task_id, stage)` verified.

**Files to Modify/Create:**
- `tests/integration/test_lifecycle_v03_rejection.py` — new.

---

### T-262: AC-4 e2e — pause-for-implementation idempotency (signal before + after pause)

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-256

**Description:**
Integration test for `request_implementation` pause/resume:

1. **Signal after pause** (normal case): run dispatches `request_implementation`; suspends; signal arrives; run resumes.
2. **Signal before pause** (race): signal arrives before `request_implementation` dispatches; the supervisor buffers it; when the dispatch fires, it finds the signal already present and resumes immediately.
3. **Duplicate signal**: two POSTs with the same `(run_id, name, task_id)`; second returns `202 + meta.alreadyReceived=true`; supervisor wakes once.

**Rationale:**
AC-4. Pause/resume is operator-facing; v0.3.0 must match v0.1.0 contract bit-for-bit.

**Acceptance Criteria:**
- [ ] All three cases pass.
- [ ] Trace shows the dispatch suspended once and woken once per logical signal.

**Files to Modify/Create:**
- `tests/integration/test_lifecycle_v03_pause_resume.py` — new.

---

### T-263: AC-8 exhaustive branch-walk — no LLM client instantiated

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-251, T-253

**Description:**
Unit test that walks every transition declared in `agents/lifecycle-agent@0.3.0.yaml` and exercises the resolver on a synthetic dispatch envelope for each branch. Asserts:

1. Every transition is reachable; every branch outcome maps to exactly one successor.
2. The resolver call does **not** instantiate any LLM client (no `core.llm.get_llm_provider` call; no `anthropic.Anthropic` import). Captured by patching `core.llm.get_llm_provider` to raise on call.
3. Reuses FEAT-009's `tests/test_runtime_deterministic_is_pure.py` import-quarantine guard idea — but at the level of the walk, not the loop module.

**Rationale:**
AC-8. The whole point of `flow.policy: deterministic` is that branch resolution is a pure function; this test makes that property a permanent guarantee for v0.3.0.

**Acceptance Criteria:**
- [ ] Test enumerates every transition in v0.3.0; each branch outcome produces exactly one successor.
- [ ] `core.llm.get_llm_provider` is not called during the walk (assertion: patched to raise).
- [ ] Test runs in CI as part of the standard suite.

**Files to Modify/Create:**
- `tests/test_lifecycle_v03_branch_walk.py` — new.

---

### T-264: AC-7 regression bar — v0.1.0 LLM-policy e2e suite passes unchanged

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-254

**Description:**
Run the existing `lifecycle-agent@0.1.0` LLM-policy + engine integration suite after every PR in this FEAT. No edits to the test, no edits to the v0.1.0 YAML, no edits to the v0.1.0 tool source.

This task is "run the existing suite + record results in the closing PR body." Failure here is a stop-and-fix per brief Section 10.

**Rationale:**
AC-7. The regression bar is non-negotiable; FEAT-011 is purely additive.

**Acceptance Criteria:**
- [ ] All v0.1.0 e2e tests pass with zero modifications.
- [ ] Closing PR (PR 5) body records the v0.1.0 suite result alongside v0.3.0 results.
- [ ] If the v0.1.0 suite fails at any point, work pauses until the cause is identified and fixed (likely a defect in T-254 bootstrap wiring or T-256 `HumanExecutor` extension).

**Files to Modify/Create:**
- None — verification + PR body note only.

---

### T-265: AC-6 coverage validator — refuses to boot on missing v0.3.0 executor

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-254

**Description:**
Extend the FEAT-009 coverage validator tests so v0.3.0's binding count is asserted at lifespan startup. Add a fixture variant that omits one v0.3.0 binding (e.g. `generate_plan`) and asserts lifespan startup raises `ExecutorCoverageError` naming the unbound `(agent_ref, node_name)`. Add another variant with `no_executor("≥10-char reason")` exemption that boots successfully.

**Rationale:**
AC-6. The coverage validator is mode-agnostic by design; this task is the proof against the v0.3.0 binding set, not new validator logic.

**Acceptance Criteria:**
- [ ] Missing-binding fixture → lifespan raises `ExecutorCoverageError` naming the node.
- [ ] Same agent with `no_executor` exemption → lifespan boots successfully.
- [ ] Bootstrap log line includes the v0.3.0 binding count.

**Files to Modify/Create:**
- `tests/modules/ai/executors/test_coverage_lifecycle_v03.py` — new.

---

### T-266: AC-9 live-LLM contract test — `@pytest.mark.live`, off by default

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-260

**Description:**
Contract test under `tests/contract/test_lifecycle_v03_live.py`, gated by `@pytest.mark.live` (off by default; CI does not run it). Drives `lifecycle-agent@0.3.0` end-to-end against the **real** Anthropic provider, producing a real brief + real plan + real review for a throwaway work item. Demonstrates the LLM-content executor path works against the real provider, not just the stub.

Engine remains stubbed via `respx` — this is an LLM-side contract test, not an end-to-end-against-real-engine test.

Cost ceiling: enforce `ANTHROPIC_MAX_TOKENS` per the existing CLAUDE.md guidance; budget the test to one full lifecycle run.

**Rationale:**
AC-9. Stub-only validation can't catch real-provider contract drift; this test is the operational backstop. Off-by-default keeps CI cost zero.

**Acceptance Criteria:**
- [ ] Test in `tests/contract/test_lifecycle_v03_live.py` with `@pytest.mark.live`.
- [ ] Drives v0.3.0 end-to-end against real Anthropic; engine stubbed.
- [ ] Asserts run reaches `RunStatus.COMPLETED` and aux rows materialize.
- [ ] Test is excluded from default `pytest` invocation; documented in PR body and `tests/contract/README` (if exists).

**Files to Modify/Create:**
- `tests/contract/test_lifecycle_v03_live.py` — new.

---

## Polish & docs

### T-267: Migration doc — `lifecycle-v01-to-v03.md`

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-253, T-254

**Description:**
Author `docs/migration/lifecycle-v01-to-v03.md` covering:

- How to run a single work item through v0.3.0 (`uv run orchestrator run lifecycle-agent@0.3.0 ...`).
- What changes operationally: nothing externally — same `/api/v1/runs` surface, same signal endpoint, same webhook contract.
- What changes internally: deterministic policy, executor seam, no LLM call in the loop, system prompts moved into executor configuration.
- What to verify before flipping the default agent ref (smoke test, cost comparison, side-by-side trace inspection).
- Edge cases from brief Section 9: branch-expression-can't-capture, mid-run upgrade (out of scope), `LifecycleMemory` schema drift, engine 404 (BUG-002 inheritance), schema-validation failure on LLM output.
- Pointer to FEAT-012 for the planned aux-write outbox unification.

**Rationale:**
Brief Section 4.1 — migration doc is part of the FEAT, not a follow-on. AC-7 + this doc together define "v0.3.0 is operationally equivalent."

**Acceptance Criteria:**
- [ ] Doc exists at `docs/migration/lifecycle-v01-to-v03.md`.
- [ ] Covers all six bullet points above.
- [ ] Cross-linked from `CLAUDE.md` Quick Reference.

**Files to Modify/Create:**
- `docs/migration/lifecycle-v01-to-v03.md` — new.
- `CLAUDE.md` — Quick Reference cross-link.

---

### T-268: Closing docs sweep — CLAUDE.md, FEAT-005 supersession note, changelog entries

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-260, T-261, T-262, T-263, T-264, T-265, T-266, T-267

**Description:**
Walk the project docs and propagate the FEAT-011 shape:

- `CLAUDE.md` — Quick Reference directory map adds `agents/lifecycle-agent@0.3.0.yaml` and `src/app/modules/ai/executors/llm_content.py` + `prompts/lifecycle/`. Patterns: add "LLM-content nodes register an `LLMContentExecutor` via `register_lifecycle_v03`; the runtime loop never imports `core.llm`." Anti-Patterns: add "Don't add a `policy.systemPrompts` block on a deterministic agent — system prompts live with their executor."
- `docs/work-items/FEAT-005-lifecycle-agent.md` — append "v0.3.0 supersedes v0.1.0 (FEAT-011, 2026-04-26). v0.1.0 preserved for migration window; deletion is a future FEAT." (v0.1.0 NOT yet deleted.)
- `docs/data-model.md` — confirm no schema changes; changelog entry referencing FEAT-011 (purely operational additions, no entity changes).
- `docs/api-spec.md` — confirm no endpoint changes; changelog entry referencing FEAT-011 (`agent_ref=lifecycle-agent@0.3.0` accepted).
- `docs/work-items/FEAT-011-lifecycle-agent-deterministic-port.md` — flip Status to `Completed`.

**Rationale:**
Brief Section 4.1 — doc sweep is part of the FEAT. CLAUDE.md doc-maintenance discipline applies (every entity / endpoint / pattern change updates the corresponding doc in the same PR).

**Acceptance Criteria:**
- [ ] All five docs updated; each touched doc carries a current-date changelog entry referencing FEAT-011.
- [ ] FEAT-005 carries the supersession note; v0.1.0 file paths unchanged on disk (deletion is a future FEAT).
- [ ] FEAT-011 brief Status flipped to `Completed`.
- [ ] CLAUDE.md Pre-Work Checklist remains valid (no broken file paths).

**Files to Modify/Create:**
- `CLAUDE.md`
- `docs/work-items/FEAT-005-lifecycle-agent.md`
- `docs/data-model.md`
- `docs/api-spec.md`
- `docs/work-items/FEAT-011-lifecycle-agent-deterministic-port.md`

---

## Summary

**Total task count: 19** (T-250 through T-268).

By type:
- Backend: 8 (T-251, T-252, T-253, T-254, T-255, T-256, T-257, T-258, T-259 — T-259 is partly verification)
- Testing: 7 (T-260, T-261, T-262, T-263, T-264, T-265, T-266)
- Documentation: 3 (T-250, T-267, T-268)

Complexity distribution:
- S: T-251, T-259, T-264, T-265, T-267
- M: T-250, T-254, T-255, T-256, T-257, T-258, T-261, T-262, T-263, T-266, T-268
- L: T-252, T-253, T-260
- XL: none.

**Critical path** (longest dependency chain — also the recommended landing order):
T-250 → T-251 → T-252 → T-253 → T-254 → T-260 → T-261 → T-268

That is: design doc → predicates → `LLMContentExecutor` → v0.3.0 YAML → bootstrap wiring → happy-path + budget e2e → rejection e2e → closing docs.

T-255 / T-256 / T-257 / T-258 (semantics) sit on T-254 and can land in parallel within their PR. T-263 (branch walk) sits on T-251 + T-253 and can land alongside T-254. T-264 (v0.1.0 regression bar) is a verification gate every PR runs against. T-266 (live-LLM contract test) is the closing test; T-267 + T-268 are the closing docs sweep.

**Risks / open questions**

- **`LifecycleMemory` shape (1 vs 2) is the single biggest decision in this FEAT.** T-250 must resolve it before T-253 starts. Plan recommends shape (1) — see plan doc — but the design doc is the authoritative call.
- **Mapping table completeness.** The brief is silent on whether *every* engine transition (W1–W6, T1–T12) is exercised by a single run, or whether some are skipped under default flow. T-250 must enumerate the actual set; if a transition is unreachable in the default flow, document it explicitly so reviewers don't expect it in T-260's transition-order assertion.
- **`LLMContentExecutor` mode literal.** Recommendation: `mode="local"` (the LLM call is in-process; no wake required). If a future FEAT needs async LLM calls, that's a new mode (`"llm_async"` or similar), not a retrofit on `LLMContentExecutor`.
- **`HumanExecutor` extension scope.** T-256 may require minimal extension to support `(run_id, name, task_id)` keying; if `HumanExecutor` already keys by signal payload, T-256 collapses to verification + tests. Implementation plan on pickup confirms.
- **System-prompt source.** T-254 copies prompt text from `.ai-framework/prompts/` into `src/app/modules/ai/executors/prompts/lifecycle/` rather than symlinking. Rationale: v0.1.0 retirement should not silently mutate v0.3.0 behavior. Trade-off: prompt drift between the two locations is a new maintenance surface; document in T-267's migration doc.
- **AC-9 cost.** Live-LLM contract test runs a full lifecycle against real Anthropic; bound the budget per run via `ANTHROPIC_MAX_TOKENS`. If a single run exceeds reasonable cost (e.g. > $1), break the test into smaller per-node contract tests rather than one full lifecycle.
