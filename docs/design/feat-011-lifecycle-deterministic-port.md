# FEAT-011 — Deterministic lifecycle agent port (`lifecycle-agent@0.3.0`)

**Status:** Accepted (PR 1 / Foundation) · **Date:** 2026-04-26 · **Sequel to:** [`feat-009-pure-orchestrator.md`](./feat-009-pure-orchestrator.md), [`feat-010-engine-executor.md`](./feat-010-engine-executor.md). Reuses every surface introduced by [`feat-008-engine-as-authority.md`](./feat-008-engine-as-authority.md).

> **Scope of this document.** This is the architectural decision behind FEAT-011 — the production lifecycle ported from LLM-as-policy onto the deterministic-flow seam. It does not yet fully specify per-task wiring (T-254 bootstrap); it pins the three load-bearing artefacts that PR 2+ depend on: the reachable-transition set, the node-to-engine-transition mapping table, the `LifecycleMemory` shape decision, and the branch-predicate inventory.

---

## Context

`lifecycle-agent@0.1.0` (FEAT-005) is the only agent driving real production work end-to-end against the flow engine. It runs on `flow.policy: llm`: the runtime calls the LLM each iteration, the LLM picks a tool, the runtime executes the matching local handler against `LifecycleMemory`, and the loop advances.

FEAT-009 proved the seam composes — `lifecycle-agent@0.2.0` is a 2-node demo on `flow.policy: deterministic`. FEAT-010 added the `EngineExecutor` so a deterministic agent can advance engine state. The pieces are in place; what's missing is a deterministic agent doing the real lifecycle workload.

The principle this FEAT lands: **orchestrator orchestrates, executors execute, the LLM is a tool inside specific executors that need it** — never at the loop layer. Brief synthesis, task planning, plan generation, and review judgement remain LLM-driven, but the LLM lives behind the executor seam (`LLMContentExecutor`, T-252), not at the policy layer.

The principle also surfaces an asymmetry the brief did not flag explicitly: **v0.1.0 lifecycle tools are entirely local**. None of the eight tool handlers (`load_work_item.py`, `generate_tasks.py`, `assign_task.py`, `generate_plan.py`, `wait_for_implementation.py`, `review_implementation.py`, `corrections.py`, `close_work_item.py`) calls `FlowEngineLifecycleClient`. They edit markdown, mutate `LifecycleMemory`, and that's it. The engine state machine (W1–W6 / T1–T12) is driven by external signal callers — the `/api/v1/signals/*` endpoints in `lifecycle/service.py` invoked by GitHub PR webhooks, operator-injected approvals, and the FEAT-008 reactor's own derivations.

That asymmetry has two consequences for v0.3.0:

1. v0.3.0 cannot trivially "wrap each v0.1.0 tool in an executor and add a `transition_key`" — there is no engine call to wrap.
2. v0.3.0 either (a) preserves the asymmetry (engine state stays driven by external signals; v0.3.0 nodes are all `local` LLM-content / `human`), or (b) closes it (v0.3.0 nodes that mark a lifecycle stage *also* drive the engine forward).

This doc picks **(b) — close the asymmetry** — for nodes where the agent is the natural caller (work-item create, task assign, task plan submit, implementation submit, work-item close). Pure LLM-content nodes (brief synthesis, plan generation, review judgement) and the human-pause node remain non-engine-bound. The reachable-transition set below names the cut explicitly.

---

## Reachable transition set (v0.1.0)

The brief's framing — "every node that today calls `FlowEngineLifecycleClient` inline" — does not match the v0.1.0 codebase. Trace through `tools/lifecycle/*.py`: every handler ends with a `LifecycleMemory.model_copy(...)` or a markdown write, never an engine HTTP call. The engine is driven by:

- **Signal endpoints** in `modules/ai/lifecycle/service.py` (e.g. `propose_task_signal`, `approve_task_signal`, `submit_plan_signal`, `submit_implementation_signal`) — invoked from outside the agent loop (GitHub webhooks, operator API calls).
- **Reactor derivations** in `modules/ai/lifecycle/reactor.py` (W2 first-task-approved, W5 all-tasks-terminal).
- **Workflow bootstrap** in `modules/ai/lifecycle/bootstrap.py` (one-time engine workflow registration).

That means a "default v0.1.0 run" — agent loop only, no external callers — *reaches zero engine transitions*. Engine state advances only via external signals. The agent observes engine state (when it eventually queries) but does not drive it.

For FEAT-011 we therefore enumerate the transition set that v0.3.0 *intends to reach* under decision (b) above — the set the agent will newly drive when ported. This is a deliberate scope expansion over v0.1.0 behaviour, not a 1:1 port.

### Reached by v0.3.0 (in scope for the mapping table)

| Transition | When fired | v0.3.0 node | Notes |
|---|---|---|---|
| `work_item.W1` | Agent loads the work item brief and registers it with the engine | `load_work_item` | New: v0.1.0 only parses the markdown into memory |
| `task.T1` × N | Agent proposes each task generated from the brief | `generate_tasks` (post LLM-content production) | New: v0.1.0 writes `tasks/<id>-tasks.md`, no engine call |
| `task.T2`+`T4` | Agent self-approves the proposal (operator approval is the human-loop variant) | `generate_tasks` (same dispatch, sequential) | New |
| `task.T5` | Agent assigns each task to its executor | `assign_task` | New: v0.1.0 records assignment in memory only |
| `task.T6` | Agent submits the generated plan | `generate_plan` | New: v0.1.0 writes `plans/plan-<task>-<slug>.md`, no engine call |
| `task.T7` | Agent self-approves the plan (operator approval is the human-loop variant) | `generate_plan` (same dispatch, sequential) | New |
| `task.T9` | Agent submits implementation when the human-loop signal arrives | `request_implementation` (on resume) | New: v0.1.0 only records that the signal arrived |
| `task.T10` | Review verdict `pass` advances the task | `review_implementation` (pass branch only) | New |
| `work_item.W6` | Agent closes the work item | `close_work_item` | New: v0.1.0 edits the brief markdown only |

**W2 and W5 are not reached by the agent.** They are reactor derivations on the engine side: W2 fires automatically when the first task is approved (T2 round-trip); W5 fires automatically when every task is in a terminal state. v0.3.0 inherits this behaviour unchanged. The agent observes the resulting webhook but does not call the transition.

### Defined-but-unreached (out of scope for v0.3.0 mapping)

| Transition | Why unreached | Recovery path if needed |
|---|---|---|
| `work_item.W3` (`in_progress -> locked`) | Admin pause; not part of automated agent flow | External signal endpoint stays the path |
| `work_item.W4` (`locked -> in_progress`) | Admin resume; mirror of W3 | External signal endpoint stays the path |
| `task.T3` (`proposed -> proposed` rejection) | Operator-injected proposal rejection; agent does not self-reject its own proposals | Aux-row write via signal endpoint (FEAT-008 inline-write contract) |
| `task.T8` (`plan_review -> planning` rejection) | Operator-injected plan rejection | Same |
| `task.T11` (`impl_review -> implementing` rejection) | Operator-injected impl rejection (but: see `correct_implementation` below) | Same |
| `task.T12` (`* -> deferred`) | Operator-injected deferral | Same |

> **Note on `correct_implementation` and rejection.** v0.1.0's `corrections` tool does not call the engine — it increments `memory.correction_attempts[task_id]` and the LLM picks `wait_for_implementation` next. Under v0.3.0 the equivalent node (`correct_implementation`) is a `LocalExecutor` that returns `result.outcome="rejected"`; the resolver routes via the new `task_rejected` predicate (see below). The rejection still does not call the engine — the FEAT-008 inline-write contract is preserved bit-for-bit. T-258 implements this; T-261 verifies zero engine POSTs on the rejection branch.

**Total: 9 distinct engine transition kinds reached (W1, W6, T1, T2+T4, T5, T6, T7, T9, T10). Six defined transitions are out of scope (W3, W4, T3, T8, T11, T12).**

---

## Node-to-executor mapping table (v0.3.0)

Eight nodes — same count as v0.1.0, with two renames per the FEAT-009 imperative-verb convention (`wait_for_implementation` → `request_implementation`, `corrections` → `correct_implementation`). Final names land in T-253; the table below is the contract T-253, T-254 and the test in T-263 must satisfy.

| v0.3.0 node | Executor mode | Engine `transition_key`(s) | LLM-content `result_schema` | Branch on |
|---|---|---|---|---|
| `load_work_item` | composite: `local` LLM-content (brief synthesis) **then** `engine` (W1) | `work_item.W1` | `LoadWorkItemResult` (placeholder) | — (1→1 to `generate_tasks`) |
| `generate_tasks` | composite: `local` LLM-content (task list) **then** `engine` (T1×N + T2+T4) | `task.T1`, `task.T2`+`T4` | `GenerateTasksResult` (placeholder) | — (1→1 to `assign_task`) |
| `assign_task` | `engine` | `task.T5` | — | — (1→1 to `generate_plan`) |
| `generate_plan` | composite: `local` LLM-content (plan markdown) **then** `engine` (T6 + T7) | `task.T6`, `task.T7` | `GeneratePlanResult` (placeholder) | `unplanned_tasks_remaining` — self-loop until false, then to `request_implementation` |
| `request_implementation` | `human` (signal name `implementation-complete`); on resume, `engine` (T9) | `task.T9` | — | — (1→1 to `review_implementation`) |
| `review_implementation` | `local` LLM-content (verdict + feedback); on `pass`, also `engine` (T10) | `task.T10` (pass branch only) | `ReviewImplementationResult` (`{verdict: 'pass'\|'fail', feedback: str}`) | `review_passed` — true to `close_work_item` (when last task) or back to `generate_plan` self-loop, false to `correct_implementation` |
| `correct_implementation` | `local` (writes `Approval` row inline; no engine call) | — (rejection: FEAT-008 inline-write contract) | `CorrectImplementationResult` (`{outcome: 'approved'\|'rejected', task_id: str}`) | `correction_attempts_under_bound` — true to `request_implementation`, false to `terminate_correction_budget` |
| `close_work_item` | `engine` | `work_item.W6` | — | terminal |

**"Composite" nodes** (`load_work_item`, `generate_tasks`, `generate_plan`, `request_implementation`, `review_implementation`) need an LLM-content (or human) result *plus* an engine transition. Three implementation options for T-254 to pick:

1. **One executor per node, internally chaining LLM call + engine transition** — register a custom adapter per composite node. Simplest YAML; most code per node.
2. **Two adjacent nodes per concern** (e.g. `generate_plan_content` → `submit_plan` engine node) — splits the composite into two transitions. Cleanest separation; requires renaming and doubles node count.
3. **`LLMContentExecutor` returns its result, executor harness chains an `EngineExecutor` step before completion** — needs runtime support for chained dispatches that does not exist today.

T-254's plan picks one. The mapping table above is option-agnostic — it lists the engine transitions each *node* is responsible for, regardless of how T-254 wires them.

> **Open question for T-254.** Option (1) keeps the YAML eight-node and matches the brief's "eight nodes" framing. Option (2) is closer to v0.2.0's shape and lets each node have a single executor mode. The author of T-254's implementation plan chooses; this design doc does not pre-bind. Whichever wins, the reachable transition set above is unchanged.

### Operator-vs-agent approval

T2+T4 and T7 above show the agent self-approving its own proposals and plans. v0.1.0 has no operator-approval gate at these stages — the LLM picks the next tool, the run advances. v0.3.0 preserves that default (`flow.policy: deterministic` does not add gates the previous policy didn't have). When a future FEAT introduces operator approval at proposal/plan stages, those become signal-driven `human` nodes — symmetric to `request_implementation` — and T2+T4 / T7 move out of `generate_tasks` / `generate_plan`'s scope into their own approval nodes.

---

## `LifecycleMemory` shape decision

**Decision: shape (1) — typed schema persisted in `RunMemory.data` under a stable namespace; executors read/write via helpers in `tools/lifecycle/memory.py`.**

### Rationale

1. **Behavioural drift minimisation.** v0.1.0's `LifecycleMemory` is a tight Pydantic v2 schema with six fields (`work_item`, `tasks`, `current_task_id`, `review_history`, `files_touched_per_task`, `correction_attempts`). Field names are referenced in three places: tool handlers, the LLM-policy stop-conditions (`correction_budget_exceeded` reads `memory.correction_attempts`), and the predicates (`unplanned_tasks_remaining` reads `memory.tasks` / `memory.plans`). Shape (2) — fold into `RunMemory` generic dict + `__memory_patch` — would force every executor (and the predicates) to switch to dict-key access, which is a typo surface and a behaviour-equivalence-test surface. Shape (1) keeps the schema typed and writes flow through helpers; the field names stay the same.
2. **Migration is one-shot.** The shape (1) port adjusts a handful of helpers (`from_run_memory`, `to_run_memory`, plus two new helpers `read_lifecycle_memory(run_memory)` and `write_lifecycle_memory(run_memory, model)` keyed on `lifecycle.v1` namespace). Shape (2) churn would touch every executor body. Shape (1) is the lower-risk port for FEAT-011's scope.
3. **The predicate registry already reads memory as `Mapping[str, Any]`.** That contract is shape-agnostic. Shape (1) provides the mapping by serialising `LifecycleMemory` through the helper before the resolver runs; shape (2) provides it natively. Either way the predicates do not change. The decision falls on the *executor* read/write surface, which shape (1) keeps narrow.

### What migrates if the choice flips later

If a future FEAT (likely FEAT-012's outbox unification) finds shape (1) too constraining — typically because aux-write outbox enrichment wants to thread untyped per-stage state — the migration is:

- Replace each `read_lifecycle_memory(run_memory)` / `write_lifecycle_memory(run_memory, model)` call with the equivalent `__memory_patch` envelope return.
- Remove the `LifecycleMemory` Pydantic schema (kept available behind the helper as a documentation aid).
- Update the two predicates' field-name references (currently `memory.get("correction_attempts")` and `memory.get("tasks")` — already shape-(2)-compatible, no change).
- Rerun the v0.1.0 e2e suite under v0.3.0 (the regression bar — AC-7).

The migration is mechanical and bounded; nothing prevents the flip later. Shape (1) is the lower-risk choice now, not the only viable choice ever.

### Stable namespace

Helpers persist `LifecycleMemory` under `RunMemory.data["lifecycle.v1"]`. The `.v1` suffix is reserved for the schema-evolution case (Pydantic-versioned-memory). v0.3.0 ships `lifecycle.v1`; the next breaking schema change would land `lifecycle.v2` alongside, with v0.1.0 readers continuing to find `lifecycle.v1`.

---

## Branch-predicate inventory

| Multi-target transition | Predicate | Source |
|---|---|---|
| `generate_plan` self-loop vs progress to `request_implementation` | `unplanned_tasks_remaining` | Existing (FEAT-009 / T-222) |
| `correct_implementation` → `request_implementation` vs `terminate_correction_budget` | `correction_attempts_under_bound` | Existing (FEAT-009 / T-222) |
| `review_implementation` → `close_work_item` (or `generate_plan` self-loop) vs `correct_implementation` | `review_passed` | **New — T-251** |
| Approval-stage rejection branch (e.g. on `correct_implementation`'s outcome envelope) | `task_rejected` | **New — T-251** |

### New predicate contracts (T-251, this PR)

- `review_passed(memory, last)` — `True` iff `last["verdict"] == "pass"`; `False` on `"fail"`; raises `ValueError` on any other value or missing field. Reads `result.verdict` produced by `review_implementation`.
- `task_rejected(memory, last)` — `True` iff `last["outcome"] == "rejected"`; `False` on `"approved"`; raises `ValueError` on any other value or missing field. Reads `result.outcome` produced by approval-stage nodes (e.g. `correct_implementation`).

> **Note on the error type.** The brief specified `PredicateError`, but the existing predicates and resolver use plain Python exceptions (`ValueError` / `KeyError` / `FlowDeclarationError`); no `PredicateError` type exists. Introducing a new error type would be a separate, larger change that would also need to update `flow_resolver._evaluate_rule`. To keep T-251 a pure registry extension (per the brief Section 10 constraint that `flow_resolver.py` not be modified), the predicates raise `ValueError` with a clear message naming the violation. Upstream LLM-content executors must constrain their output via `result_schema` so the predicate is total in practice. If a future FEAT decides to formalise the error type, the migration is one find-and-replace plus the resolver change.

### Resolver-expression form as alternative

The resolver also supports `result.<field> <op> <literal>` expressions inline (`flow_resolver._EXPR_RE`). For `review_implementation`'s pass/fail branch, `result.verdict == 'pass'` is an inline equivalent of the `review_passed` named predicate. The named-predicate form is preferred for the lifecycle agent because:

1. The error path matters — the named predicate raises on unexpected values; the inline expression silently routes to the `false` target on `verdict == "weird"`. For an operator-facing surface, "fail loudly" is the correct default.
2. The two predicates are referenced from multiple branches (T-251's `review_passed` is shared between `review_implementation`'s pass-branch and any future review-style stage); a named registry entry keeps the spelling honest.

T-253's YAML uses the named predicates. If a v0.3.0-specific branch surfaces during T-253 implementation that the named-predicate form genuinely cannot capture (extremely unlikely with two predicates of this shape), the resolver's inline expression form is the fallback documented at the top of `flow_predicates.py`.

---

## AC-7 regression bar (every PR)

The v0.1.0 LLM-policy end-to-end test suite must pass unchanged in every PR landing FEAT-011 work. Failure is a **stop-and-fix**, not a deferred ticket — the brief Section 10 names this explicitly. The most likely culprit for a v0.1.0 regression in a FEAT-011 PR is the bootstrap wiring (T-254) or the `HumanExecutor` extension (T-256); revert + diagnose is the recovery.

PR 1 (this PR — T-250 + T-251) is pure additions: a design doc, two new predicates registered in `flow_predicates.py`, no agent file, no executor file, no schema change. The v0.1.0 suite is untouched by definition.

---

## Restart safety (forward note for T-259)

v0.3.0 inherits FEAT-010's `reconcile-dispatches` (engine-mode dispatches resumed via the engine's transition history) and FEAT-008's `reconcile-aux` (orphan aux outbox rows). The mapping table above shows engine-bound dispatches in five of the eight nodes; each of those is covered by `reconcile-dispatches`. `LLMContentExecutor` dispatches are local-mode and do not write outbox rows — an interrupted LLM call is recovered by the runtime restart re-dispatching the node. T-259 verifies this against a v0.3.0 fixture; if a gap surfaces (e.g. an in-flight LLM call leaks a partial markdown write), the fix is scoped in T-259, not a new reconciler.

---

## Cross-references

- Forward-link this doc from `CLAUDE.md` Patterns: "LLM-content nodes register an `LLMContentExecutor`; the runtime loop never imports `core.llm`. Each LLM-content node carries a `system_prompt` + `result_schema`; the LLM is a tool inside the executor, not at the runtime layer."
- Forward-link from `docs/design/feat-009-pure-orchestrator.md`: "FEAT-011 ports the production lifecycle onto this seam."
- Forward-link from `docs/design/feat-010-engine-executor.md`: "FEAT-011 is the first real consumer of the `EngineExecutor` seam."

## Open questions surfaced by this design pass

1. **Composite-node implementation shape (T-254).** Three options listed above (custom adapter / split nodes / chained dispatch). The mapping table is option-agnostic; T-254's implementation plan picks one. Recommend option (1) — simplest YAML, matches the brief's eight-node framing.
2. **v0.1.0 → v0.3.0 behavioural delta.** v0.1.0 reaches zero engine transitions from the agent loop; v0.3.0 reaches nine. This is a deliberate scope expansion — the doc names it explicitly so reviewers don't read it as a 1:1 port. T-264's regression bar protects v0.1.0; it does not require v0.3.0 to mirror v0.1.0's no-engine-call behaviour.
3. **Operator approval at proposal/plan stages.** v0.3.0 self-approves T2+T4 and T7. If product wants a human-in-the-loop gate at those stages, that's a follow-on FEAT, not a v0.3.0 scope item. The mapping table flags the symmetry with `request_implementation`.
