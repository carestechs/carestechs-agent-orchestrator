# FEAT-015 — Manual lifecycle variant (`lifecycle-agent@0.4.0-manual`)

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | FEAT-015 |
| **Name** | Manual lifecycle variant — human checkpoints at every LLM→engine seam |
| **Target Version** | Continuous |
| **Status** | Proposed |
| **Priority** | High |
| **Requested By** | Carlos (operator-driven flow trial) |
| **Date Created** | 2026-05-13 |

---

## 2. User Story

**As an** operator who wants close oversight of an agent-driven run, **I want to** select a lifecycle agent variant that pauses for my approval at every transition that commits state to the flow engine (brief, task list, plan, review), **so that** the LLM proposes and I dispose — every artefact reaching the engine has been seen and approved by a human, and the run cannot silently advance past a stage where I want to intervene.

---

## 3. Goal

A second lifecycle agent (`lifecycle-agent@0.4.0-manual`) coexists with `@0.3.0` and is selected at run start via the existing `agent_ref` parameter. The flow graph is byte-identical to `@0.3.0` except for four inserted human-checkpoint nodes and one swapped reviewer node. No data-model, no engine-side, no protocol changes — the variant rides on existing `HumanExecutor` infrastructure plus a small `memory_patch_builder` extension that lets the operator's signal payload edit `LifecycleMemory` before the next node fires.

---

## 4. Feature Scope

### 4.1 Included

- **New agent YAML** `agents/lifecycle-agent@0.4.0-manual.yaml`. Same eight-stage skeleton as `@0.3.0` with the following deltas:
  - **+4 new nodes:** `confirm_brief`, `confirm_tasks`, `confirm_plan`, and `human_review_implementation` (replaces the LLM `review_implementation`).
  - **Modified `flow.transitions`:** four insertions (`load_work_item → confirm_brief → register_work_item`, `generate_tasks → confirm_tasks → propose_tasks`, `generate_plan → confirm_plan → approve_plan`, `submit_implementation → human_review_implementation`) and one substitution (the `review_implementation` branch is rewired to fire on `human_review_implementation`'s `verdict` instead of an LLM result).
  - **`description` and `intakeSchema`** unchanged from `@0.3.0` — same upload shape (`intake.workItem = {id, kind, content?}`).
- **Bootstrap entry point** `register_lifecycle_v04_manual(...)` in `executors/bootstrap.py`, registered from `lifespan.py` alongside `register_lifecycle_v03`. Reuses every `@0.3.0` binding (engine create, propose-tasks fanout, sequence-close, etc.) and adds the four new human bindings plus the swapped reviewer.
- **Refactor `register_lifecycle_v03`** to take an `agent_ref` parameter (default `"lifecycle-agent@0.3.0"`) and a `skip_review_implementation: bool = False` flag — so the v0.4.0-manual entry point can call the v0.3.0 helper to install the shared bindings under the new ref, and then add its own human reviewer in place of the LLM one. No behavior change for any existing caller.
- **`HumanExecutor.memory_patch_builder`** — a new optional `Callable[[Mapping[str, Any]], Mapping[str, Any]]` constructor argument on `HumanExecutor`. When set, the executor takes the operator-delivered signal payload, calls the builder, and returns the resulting dict as `result.__memory_patch` — which the runtime then merges into `RunMemory.data` via the existing `_write_state` pipeline. When unset, behavior is unchanged (raw payload surfaces in `result`).
- **Four memory-patch builders** in `executors/bootstrap.py` or a sibling helper module:
  - `_apply_brief_correction(payload)` — accepts optional `payload.workItem` overrides for `title` / `type`; returns a `{"lifecycle.v1": {"work_item": {...}}}` patch.
  - `_apply_tasks_correction(payload)` — accepts optional `payload.tasks` (a list of `{id, title, summary}` objects) and returns a patch that replaces `LifecycleMemory.tasks`.
  - `_apply_plan_correction(payload)` — accepts optional `payload.plan` (markdown string) and patches `LifecycleMemory.taskPlans[currentTaskId]`.
  - `_apply_review_verdict(payload)` — requires `payload.verdict ∈ {"pass", "fail"}`; on `fail` also reads `payload.feedback`; writes the same `reviewHistory[]` entry shape the LLM reviewer's `_patch_review` writes today. This is the load-bearing one — keeping the shape identical means downstream `review_passed` predicate + `correct_implementation` node read the same memory contract as v0.3.0.
- **Operator signal contracts** documented in `docs/api-spec.md`:
  - `brief-confirmed` (no `taskId`) — payload optional.
  - `tasks-confirmed` (no `taskId`) — payload optional, may carry edited task list.
  - `plan-confirmed` (`taskId` required) — payload optional, may carry edited plan markdown.
  - `review-completed` (`taskId` required) — payload required, must carry `verdict` and optional `feedback`.
- **Integration test** `tests/integration/test_lifecycle_v04_manual.py` driving the full manual loop end-to-end with a stubbed `FlowEngineLifecycleClient`, scripted signal delivery, and assertions that (a) each checkpoint flips `Run.status` to `paused` and back; (b) signal payloads correctly mutate `LifecycleMemory`; (c) the `propose_tasks` executor commits the *edited* task list, not the LLM original; (d) the final run terminates at `close_work_item` with the expected memory shape.
- **Documentation updates:**
  - `docs/ARCHITECTURE.md` — short note on lifecycle agent variants and the `register_lifecycle_v04_*` pattern.
  - `CLAUDE.md` — Quick-Reference command update (`uv run orchestrator run lifecycle-agent@0.4.0-manual --work-item ...`).
  - `agents/README.md` (if one exists; otherwise inline notes in each YAML) — variant index.

### 4.2 Excluded

- **`lifecycle-agent@0.4.0-auto`** — the auto variant is the *next* feature (FEAT-016). The two variants peer-coexist after both ship, but they're separate features so the manual flow can land and be exercised on its own.
- **Editing the engine task-list shape from the human checkpoint.** `confirm_tasks` rewrites `LifecycleMemory.tasks`; `propose_tasks` then commits the edited list to the engine. No "engine-only" task additions or deletions outside of `propose_tasks`.
- **Replaying / rewinding a paused run.** Once a checkpoint signal is delivered, the flow advances. Operators cannot retroactively un-approve a brief. Future feature, not in scope here.
- **A UI for the checkpoints.** This FEAT ships the API contracts (signal payloads). A UI consumer is a parallel work item.
- **Per-checkpoint timeouts.** Human-pause timeout is currently unbounded by default per the recent fix. Adding per-checkpoint SLA deadlines (e.g., "brief approval must arrive within 24h") is a follow-up.
- **Unbounded correction budget for the manual variant.** The current `LIFECYCLE_MAX_CORRECTIONS=2` cap applies. Operator-driven flows might want unbounded retries — captured as an open design question (Section 9) and a candidate follow-up, not landed here.

---

## 5. Acceptance Criteria

- **AC-1**: `POST /api/v1/runs` with `agentRef: "lifecycle-agent@0.4.0-manual"` and a valid `intake.workItem` returns 202 and starts a run that advances to `load_work_item`, then parks at `confirm_brief` with `Run.status = paused`.
- **AC-2**: Delivering `POST /api/v1/runs/{runId}/signals { name: "brief-confirmed" }` (no payload) resumes the run; the next dispatch is `register_work_item` (engine W1), and the engine work item is created with the LLM-synthesized title and type unchanged.
- **AC-3**: Delivering `brief-confirmed` with `payload.workItem = { title: "Edited", type: "BUG" }` resumes the run; the engine work item created at `register_work_item` uses the edited values; `LifecycleMemory.work_item.title === "Edited"`.
- **AC-4**: After `generate_tasks` produces N task descriptions, the run parks at `confirm_tasks`. Delivering `tasks-confirmed` with `payload.tasks` replaced (M ≠ N tasks) resumes the run; `propose_tasks` creates exactly M engine tasks, matching the replacement list; `LifecycleMemory.tasks.length === M`.
- **AC-5**: For each task, the flow advances through `assign_task → generate_plan` and parks at `confirm_plan`. Delivering `plan-confirmed` with `payload.plan = "..."` patches `LifecycleMemory.taskPlans[<taskId>]`; `approve_plan` (T7) fires next; the engine task transitions `plan_review → implementing`.
- **AC-6**: After `submit_implementation` (T9), the run parks at `human_review_implementation`. Delivering `review-completed { verdict: "pass" }` advances to `approve_review` (T10) and onward. Delivering `review-completed { verdict: "fail", feedback: "..." }` routes to `correct_implementation` (no engine call), records the rejection in `LifecycleMemory.reviewHistory[]` with the same shape the LLM reviewer would have written, and loops back to `request_implementation` per the existing `correction_attempts_under_bound` predicate.
- **AC-7**: An end-to-end manual run completes against a stubbed engine with: 4 human checkpoints fired (one of each kind), all signals delivered, no LLM call to `core.llm` for the review step (verified by an import-quarantine assertion or by checking `policy_calls` rows). Run terminates at `close_work_item` with `RunStatus.COMPLETED`.
- **AC-8**: `Run.status` flips correctly across all four human checkpoint pauses: `pending → running → paused (confirm_brief) → running → paused (confirm_tasks) → running → ... → completed`. Each `paused` window matches an entry in the `dispatches` table with `mode=human` and a corresponding `run_signals` row keyed on the expected signal name.
- **AC-9**: `register_lifecycle_v03` continues to register the v0.3.0 binding set under `"lifecycle-agent@0.3.0"` unchanged. Running a v0.3.0 agent after the v0.4.0-manual is registered still uses the LLM reviewer and skips every human checkpoint. (The two flows are independent; refactoring v0.3.0's bootstrap must not regress its acceptance suite.)
- **AC-10**: `agents/lifecycle-agent@0.4.0-manual.yaml` parses cleanly via the agent loader; `validate_executor_coverage()` at lifespan startup confirms every node in the variant has a binding (or an explicit `no_executor` exemption) — boot fails fast otherwise.
- **AC-11**: `docs/api-spec.md` documents the four signal contracts (name, required `taskId` presence, payload shape per signal). `CLAUDE.md` Quick Reference includes the new agent ref in the example `orchestrator run` command line.

---

## 6. Key Entities and Business Rules

| Entity | Role in Feature | Key Business Rules |
|--------|----------------|--------------------|
| `AgentDefinition` (loaded from YAML) | A second variant peer-coexisting with `@0.3.0`. Same `intakeSchema`; flow graph differs by 4 inserted nodes + 1 swapped reviewer. | The `agent_ref` is the selection point. No runtime branching between variants — bindings are pinned per agent ref at registry time. |
| `ExecutorBinding` (per `(agent_ref, node_name)` pair) | Variant ships its own bindings for the new checkpoint nodes and reuses every `@0.3.0` binding for shared nodes. | Existing `validate_executor_coverage()` continues to gate boot — every node in every registered agent must have a binding or `no_executor` exemption. |
| `HumanExecutor` | Gains an optional `memory_patch_builder` constructor argument. When set, the operator signal's `payload` is fed through the builder to produce a `__memory_patch` dict on the returned envelope. | When unset, behavior is byte-identical to today (raw payload surfaces in `result`). Backward-compat for the existing `request_implementation` binding is preserved. |
| `RunSignal` | Four new signal names (`brief-confirmed`, `tasks-confirmed`, `plan-confirmed`, `review-completed`). Same idempotency contract as today — `(run_id, name, task_id)` dedup. | Each checkpoint's `expected_signal_name` is fixed at binding time. Duplicate deliveries return 202 with `meta.alreadyReceived=true`, matching FEAT-005's existing contract. |
| `LifecycleMemory` (`lifecycle.v1` namespace) | No schema change. The new memory-patch builders write to existing fields (`work_item`, `tasks`, `taskPlans`, `reviewHistory[]`). | The review-verdict builder MUST emit the same shape `_patch_review` does today — that's how downstream nodes stay agnostic to which reviewer (LLM or human) wrote the entry. |

**New entities required:** None. Two minor additions: `HumanExecutor.memory_patch_builder` (constructor field) and four pure builder functions. The lifecycle data model is unchanged.

---

## 7. API Impact

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/v1/runs` | POST | Unchanged | Accepts `agentRef: "lifecycle-agent@0.4.0-manual"` as one of the registered agents; otherwise byte-identical. |
| `/api/v1/runs/{runId}/signals` | POST | Unchanged contract, four new `name` values accepted | `brief-confirmed`, `tasks-confirmed`, `plan-confirmed`, `review-completed`. Each documented with its required-fields + payload shape in `docs/api-spec.md`. Existing `implementation-complete` is unaffected and still used by `request_implementation`. |

**New endpoints required:** None.

---

## 8. UI Impact

| Screen / Component | Status | Description |
|--------------------|--------|-------------|
| — | — | No UI in v1; API-only contract. A future UI surface that renders the four checkpoint states is a separate work item. |

**New screens required:** None.

---

## 9. Edge Cases

- **Empty signal payload at `confirm_brief` / `confirm_tasks` / `confirm_plan`.** Means "approve without edits." The memory-patch builder returns an empty patch (`{}`); the run advances unchanged. NOT an error.
- **`tasks-confirmed` with a `payload.tasks` of length 0.** Operator wants to abort task fanout. Two design options: (a) treat as "no tasks needed, skip ahead to close_work_item" or (b) reject as a 422 from the executor. **Decision: (b) reject** — there is no production-meaningful "work item with zero tasks" path in the engine workflow; an operator who wants to cancel should call `POST /api/v1/runs/{id}/cancel` instead. The memory-patch builder validates `len(payload.tasks) >= 1` and the executor surfaces a `failed` envelope on violation, terminating the run with an error.
- **`plan-confirmed` for a `taskId` that doesn't match the run's `current_task_id`.** The signal endpoint already keys idempotency on `(run_id, name, task_id)`, so a stale or wrong `taskId` will simply not match the in-flight `await_signal` call and the run will park forever waiting for the right one. Operators see this via the trace (run is paused, no signal advance). Document the contract clearly in api-spec.md.
- **`review-completed` with `verdict: "fail"` and missing `feedback`.** Allowed; `feedback` is optional even on fail. The `_apply_review_verdict` builder writes a `reviewHistory[]` entry with `feedback: null`. Downstream `correct_implementation` records the rejection regardless.
- **`review-completed` with `verdict: "needs_changes"` or any value outside `{"pass", "fail"}`.** Rejected by the builder with a `failed` envelope; run terminates. The two-valued branch contract is locked at the YAML resolver level (`review_passed` predicate reads `result.verdict == "pass"`). Adding a third verdict is a separate design discussion (see Open Questions).
- **Operator delivers a signal for a checkpoint that has not yet been reached.** The supervisor's `_signal_buffers` already handles this: the payload is buffered and consumed when the matching `await_signal` arrives. This is preload semantics from FEAT-005 — not a manual-variant-specific concern.
- **Operator delivers a signal after the run has terminated.** Existing behavior: 202 with `meta.alreadyReceived=true` if a `run_signals` row exists; otherwise the signal-create adapter persists the row but `RunSupervisor` has already purged the signal channel and the run does not resume. Documented today; no change needed.
- **`register_lifecycle_v03` bootstrap is called twice (once for v0.3.0, once via the v0.4.0-manual helper).** The `ExecutorRegistry` raises on duplicate registration. The refactored helper takes `agent_ref` as a parameter so each call registers a distinct `(agent_ref, node_name)` key set. Duplicate-registration is a *boot-time* assertion; misconfigured wiring fails fast at lifespan startup.

---

## 10. Constraints

- **No engine-side changes.** The flow engine's workflows (`work_item_workflow`, `task_workflow`) are unchanged; the variant only changes *when* the orchestrator fires existing transitions.
- **Pydantic-at-boundary discipline (CLAUDE.md).** Each signal payload's allowed shape MUST be a typed Pydantic model — even if the route accepts arbitrary JSON today, the memory-patch builders parse through a typed schema before mutating memory.
- **Deterministic-runtime quarantine (FEAT-009).** No new import of `core.llm` from any module touched by this feature. The human reviewer's binding lives in `executors/bootstrap.py`; the deterministic runtime continues not to import an LLM SDK.
- **Engine-call discipline (FEAT-010).** No new inline `lifecycle_client.transition_item` calls. Every engine-bound transition routes through the existing `EngineExecutor` / `EngineCreateExecutor` bindings via the `register_engine_executor` pattern. Human checkpoints are pure `mode=human` dispatches; they do not call the engine themselves.
- **Idempotency contract (FEAT-005).** Each signal name's `(run_id, name, task_id)` triple is dedup'd at the signal-create adapter. Delivering the same `brief-confirmed` twice returns 202 with `alreadyReceived=true` and does not double-advance the flow.
- **Boot-fail-fast (FEAT-009).** `validate_executor_coverage()` at lifespan must catch any node in the new agent that lacks a binding. The bootstrap helper is the single source of truth for coverage.
- **Single-worker constraint (CLAUDE.md).** Like every other v1 flow, the manual variant runs under `RunSupervisor` and inherits the single-uvicorn-worker restriction. No new cross-worker coordination.

---

## 11. Motivation and Priority Justification

**Motivation:** The fully-automatic `@0.3.0` flow is useful but commits state to the engine at every stage as soon as the LLM produces an artefact — fine for well-understood briefs, risky for ambiguous ones. Operators trialing the lifecycle agent on their own work want a "watch every step" mode where the LLM proposes and they approve. The current toolset offers two extremes (fully-LLM via `@0.3.0`, fully-stubbed via the `stub-pass` reviewer for CI) and nothing in between. This FEAT lands the in-between.

**Impact if delayed:** Operators currently can't trial the lifecycle agent on production-quality briefs without committing to `@0.3.0`'s autonomy or hand-running the engine transitions themselves. Trust in agent-driven runs builds incrementally — with no manual variant, the only path is to launch a fully-automatic run and then forensically audit the result. That's a poor adoption ramp for AD-6 self-host discipline.

**Dependencies on this feature:** The auto variant (FEAT-016) follows the same `register_lifecycle_v04_*` pattern and benefits from the `register_lifecycle_v03(agent_ref=...)` refactor landing here first. Any future variant (e.g., "review-only" with auto-everything-except-review, or "brief-only" with manual confirmation just at the brief stage) reuses the same shape.

---

## 12. Traceability

| Reference | Link |
|-----------|------|
| **Persona** | `docs/personas/primary-user.md` — operator trialing the lifecycle agent on their own work items |
| **Stakeholder Scope Item** | "Headless service drives the ia-framework's feature lifecycle as an agent-driven loop" — operator-supervised variants are an explicit part of the spectrum |
| **Success Metric** | Adoption: number of manual-variant runs that complete end-to-end without falling back to manual engine intervention. Secondary: time-to-first-checkpoint-signal (how long after run start the operator engages). |
| **Related Work Items** | FEAT-005 (lifecycle agent foundation, `HumanExecutor` + signal contract), FEAT-009 (executor seam — the substrate this feature builds on), FEAT-011 (deterministic port — the v0.3.0 reference flow), IMP-003 (swappable reviewer binding — proves the reviewer slot is already designed to vary by binding), IMP-002 (paused run status — already used by every `mode=human` dispatch). Future: FEAT-016 (auto variant), follow-up for per-checkpoint SLA timeouts. |

---

## 13. Usage Notes for AI Task Generation

When generating tasks from this Feature Brief:

1. **Refactor before variant.** The `register_lifecycle_v03(agent_ref=..., skip_review_implementation=...)` refactor lands as task 1, on its own — every other task imports from it. The refactor MUST preserve v0.3.0 behavior bit-for-bit; an existence proof is the v0.3.0 acceptance suite passing unchanged.
2. **`HumanExecutor.memory_patch_builder` is task 2.** Constructor argument plus envelope-shape change. Existing `request_implementation` binding migrates to pass `memory_patch_builder=None` explicitly — no behavior change. Unit tests cover both paths.
3. **Memory-patch builders are pure functions.** Four small helpers in a sibling module (e.g., `executors/lifecycle_manual_patches.py`). Each is a pure `(payload) -> patch` function with its own Pydantic schema for input validation. Unit-testable without any session.
4. **YAML before bootstrap.** The agent YAML for `@0.4.0-manual` lands before `register_lifecycle_v04_manual` — the bootstrap function loads the agent definition to enumerate nodes. Coverage validation in tests asserts every YAML node has a binding.
5. **Integration test is the cutover proof.** A single end-to-end test driving four signals through one run is what flips this feature from "all parts built" to "feature works." Schedule it last. Pattern is in `tests/integration/test_lifecycle_v03_acceptance.py` (or equivalent) — copy the structure, swap the agent ref, add four `deliver_signal` calls between dispatches.
6. **No new endpoint, no new route.** Every signal delivery uses the existing `POST /api/v1/runs/{id}/signals`. The four new signal names are by-convention only; the route schema is unchanged.
7. **Documentation tasks (CLAUDE.md mandate).** `docs/api-spec.md` (signal contracts + payload shapes), `docs/ARCHITECTURE.md` (variant pattern), `CLAUDE.md` (Quick-Reference command line). All in the same PR as the corresponding code change.
8. **Anti-pattern entries.** None new — existing CLAUDE.md anti-patterns about engine-call discipline and runtime quarantine already cover the failure modes for new variants. No new "Don't" line required.

---

## 14. Open Design Questions (for discussion before task generation)

- **Should the manual variant relax the correction budget?** Today `LIFECYCLE_MAX_CORRECTIONS=2` terminates the run after the second rejection. For operator-driven flows, unbounded retries may be more appropriate — but it's also an invariant the budget protects against runaway loops. Suggest: keep the cap at v0.4.0-manual launch; allow per-agent override in a follow-up.
- **Three-valued reviewer verdict (`pass` / `needs_changes` / `fail`)?** Today's branch contract is binary. Adding a third arm for "ask for revisions without burning a correction slot" would require extending the YAML branch resolver from `true/false` to multi-target — a non-trivial change to `flow_resolver.py`. Suggest: defer to a follow-up; manual reviewers can communicate via `feedback` on the existing two-valued contract.
- **Should `confirm_tasks` allow operators to *add* tasks the LLM didn't generate?** Today's design swaps the whole list; adding new tasks is a special case of "replace with a longer list." The memory-patch builder accepts any task list of `len >= 1`. No special handling needed; document the affordance.
- **Naming: `@0.4.0-manual` vs `@0.4.0`-named-something-else?** The suffix is intended to peer with `@0.4.0-auto` (FEAT-016). Alternative: bump the minor twice (`@0.4.0` = manual, `@0.5.0` = auto). Suffix-on-version reads better when both exist side-by-side; the resolver doesn't care either way.
