# FEAT-015 — Task Breakdown

> **Source brief:** `docs/work-items/FEAT-015-lifecycle-manual-variant.md`
> **Numbering:** Continues from FEAT-014 (last task T-294).
> **Total tasks:** 10
> **Critical path:** T-295 → T-296 → T-297 → T-299 → T-302 (5-step chain)

---

## Foundation / Refactor

### T-295: Refactor `register_lifecycle_v03` to accept `agent_ref` and `skip_review_implementation`

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Description:**
Add two keyword-only parameters to `register_lifecycle_v03` in `src/app/modules/ai/executors/bootstrap.py`: `agent_ref: str = "lifecycle-agent@0.3.0"` (defaults preserve current behavior) and `skip_review_implementation: bool = False` (when True, the function does not register the LLM `review_implementation` binding so a sibling variant can register a human one in that slot). The function's call sites in `lifespan.py` pass nothing — defaults kick in.

**Rationale:**
The manual variant (FEAT-015 §4.1) reuses every v0.3.0 binding under a new agent ref except the reviewer slot. Refactoring is the load-bearing first step — every other task imports the resulting helper. The v0.3.0 acceptance suite passing unchanged is the existence proof that the refactor is a no-op for the existing caller (AC-9).

**Acceptance Criteria:**
- [ ] `register_lifecycle_v03(registry, ..., agent_ref="lifecycle-agent@0.3.0")` registers exactly the same `(agent_ref, node_name)` bindings as today.
- [ ] `register_lifecycle_v03(registry, ..., agent_ref="some-other-ref")` registers the same node set under the new ref, with no boot-time errors.
- [ ] `skip_review_implementation=True` omits the `review_implementation` binding; every other binding registers as usual.
- [ ] All existing tests in `tests/integration/test_lifecycle_v03_*.py` (and any other test exercising `register_lifecycle_v03`) pass with no changes.
- [ ] `pyright` clean.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/bootstrap.py` — add params; thread `agent_ref` through every `registry.register(agent_ref=..., ...)` site within the function; gate the `review_implementation` registration on `skip_review_implementation`.

**Technical Notes:**
- The `agent_ref` parameter must be threaded through every internal `registry.register` call inside `register_lifecycle_v03` — there are roughly 15-20 of them. Use a single local `_ref = agent_ref` alias to keep the diff readable.
- For the `target_id_resolver` closures (BUG-004), nothing changes — they read from `LifecycleMemory`, not from `agent_ref`.
- For the engine-create executor (BUG-003 / `register_work_item`), the workflow IDs come from constructor injection — also unchanged.
- Do NOT modify `register_lifecycle_v03`'s behavior under default args. Anti-pattern to introduce backward-compat shims (CLAUDE.md "Don't add backwards-compatibility hacks") — but this is a strict additive change to keyword-only params, which is the right shape.

---

### T-296: Add `memory_patch_builder` to `HumanExecutor`

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** None (can land in parallel with T-295)

**Description:**
Extend `HumanExecutor` in `src/app/modules/ai/executors/human.py` to accept an optional `memory_patch_builder: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None` constructor argument. When set, after receiving the operator's signal, the executor calls the builder with the signal's payload and embeds the result in the returned `DispatchEnvelope.result` under the reserved key `__memory_patch` — which the runtime then merges into `RunMemory.data` via the existing `_write_state` pipeline (see `runtime_deterministic.py` line ~420). When unset, behavior is byte-identical to today (raw payload surfaces in `result`).

**Rationale:**
The four manual-variant checkpoints need to let the operator inject corrections via the signal payload (FEAT-015 §4.1). The runtime already supports `result.__memory_patch` (line 420 of runtime_deterministic.py — `LLMContentExecutor` uses the same hook for its standalone-node memory writes). Reusing that hook keeps the changes local to `HumanExecutor`; no runtime-loop changes.

**Acceptance Criteria:**
- [ ] `HumanExecutor(ref=..., expected_signal_name=...)` (no `memory_patch_builder`) returns a `DispatchEnvelope` whose `result` matches today's shape byte-for-byte.
- [ ] `HumanExecutor(ref=..., expected_signal_name=..., memory_patch_builder=fn)` calls `fn(payload)` once on signal arrival; the returned dict is placed at `result["__memory_patch"]`.
- [ ] When `memory_patch_builder` raises any exception, the executor returns a `failed` envelope with `outcome=error` and `detail` containing the exception type + message; the run terminates with a `stop_reason=error`. (Stricter than logging-and-continuing; bad payloads should not silently corrupt memory.)
- [ ] Existing `request_implementation` binding (no `memory_patch_builder`) continues to work end-to-end (run a quick `tests/integration/test_runtime_human_pause.py` check).

**Files to Modify/Create:**
- `src/app/modules/ai/executors/human.py` — add constructor param; thread through `dispatch`.
- `tests/modules/ai/executors/test_human_executor.py` (or wherever HumanExecutor unit tests live) — new tests for both branches + the raise-during-builder failure path.

**Technical Notes:**
- The builder is sync (pure function); don't accept an awaitable. Memory patches are derived from the payload — there is no I/O reason to make them async, and async builders would force every call site to await.
- Memory patches must NOT include keys starting with `_` (the runtime already filters those out in `_write_state` line 184). Document this in the docstring.
- The builder result dict is merged shallowly into `RunMemory.data` (per `_write_state`); nested keys are replaced wholesale. For `lifecycle.v1.work_item` updates, the builder must construct the full nested patch including the `lifecycle.v1` namespace.

---

## Backend — Memory-Patch Builders + Signal Schemas

### T-297: Pydantic signal payload schemas + four memory-patch builders

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-296

**Description:**
Create `src/app/modules/ai/executors/lifecycle_manual_patches.py` with:
1. Four Pydantic v2 models for the four signal payload shapes: `BriefConfirmedPayload`, `TasksConfirmedPayload`, `PlanConfirmedPayload`, `ReviewCompletedPayload`. All use `model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)` per the project's snake/camel convention.
2. Four pure builder functions: `_apply_brief_correction(payload, *, lifecycle_memory) -> dict[str, Any]`, `_apply_tasks_correction(...)`, `_apply_plan_correction(...)`, `_apply_review_verdict(...)`. Each validates its input via the matching Pydantic model and returns a `__memory_patch` dict targeting `lifecycle.v1`.

**Rationale:**
The four checkpoints differ only in what they let the operator edit. Each builder is a small pure function with its own schema — keeping them separate makes them individually unit-testable and documents the contract for each signal. The schemas are the source of truth for §7 (`docs/api-spec.md` documents them).

**Acceptance Criteria:**
- [ ] `BriefConfirmedPayload`: `workItem: WorkItemCorrection | None` (optional; when set, `title: str | None`, `type: WorkItemType | None`). `_apply_brief_correction` returns `{}` if `payload.workItem is None`; otherwise returns `{"lifecycle.v1": {"work_item": <merged>}}` where merged carries over current memory work_item fields and overlays the provided corrections.
- [ ] `TasksConfirmedPayload`: `tasks: list[TaskItem] | None` (optional; `TaskItem` has required `id: str`, `title: str`, optional `summary: str | None`). When `tasks` is `None`, builder returns `{}`. When provided, asserts `len(tasks) >= 1` and returns `{"lifecycle.v1": {"tasks": [...]}}`. Length-0 → `ValueError` (caught by HumanExecutor per T-296 AC-3).
- [ ] `PlanConfirmedPayload`: `plan: str | None`. When `None`, returns `{}`. When provided, returns `{"lifecycle.v1": {"taskPlans": {current_task_id: plan}}}` — the builder reads `current_task_id` from the passed `lifecycle_memory`. If `current_task_id is None`, builder raises `ValueError("plan-confirmed received with no current_task_id in memory")`.
- [ ] `ReviewCompletedPayload`: `verdict: Literal["pass", "fail"]` (required), `feedback: str | None = None`. `_apply_review_verdict` returns a `__memory_patch` that appends a new entry to `lifecycle.v1.reviewHistory[]` with the same shape `_patch_review` (the LLM reviewer's builder) writes today — `{taskId, verdict, feedback, reviewedAt, reviewer: "human"}`. The `reviewer` discriminator is the only shape-level difference from the LLM path.
- [ ] Each builder has unit-test coverage in T-300; this task's AC focuses on the schemas + functions producing the correct shape.
- [ ] All schemas use camelCase JSON aliases; Python attributes are snake_case.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/lifecycle_manual_patches.py` — new module.
- `src/app/modules/ai/tools/lifecycle/memory.py` — verify `reviewHistory[]` shape and reuse the existing field name; do NOT create a parallel shape.

**Technical Notes:**
- `_apply_plan_correction` and `_apply_review_verdict` need read access to the current memory (for `current_task_id` / `reviewHistory[]` history length). Plumb this via the function signature — the HumanExecutor (T-296) holds the session_factory and can read memory before calling the builder. Alternative: pass the current `LifecycleMemory` as a second arg to the builder (cleaner, no I/O inside the pure function); the executor reads memory once and passes it in.
- Reuse `_patch_review` from `executors/llm_content.py` (or wherever the LLM reviewer's patch builder lives) if shape-sharing is cleaner — but the spec says "same shape, different writer", not "same code path". Don't refactor `_patch_review` unless needed.
- `WorkItemType` enum is in `app/modules/ai/enums.py` (the same one used by `WorkItem.type`).

---

## Agent YAML + Bootstrap

### T-298: New agent YAML `lifecycle-agent@0.4.0-manual.yaml`

**Type:** Backend (configuration)
**Workflow:** standard
**Complexity:** S
**Dependencies:** None (can land in parallel with T-295/T-296/T-297)

**Description:**
Create `agents/lifecycle-agent@0.4.0-manual.yaml`. Same eight-stage skeleton as `@0.3.0`. Insert four new nodes (`confirm_brief`, `confirm_tasks`, `confirm_plan`, `human_review_implementation`) into `nodes:` and modify `flow.transitions` per the design diff in FEAT-015 §4.1. The `review_implementation` LLM node is **removed** from this variant (replaced by `human_review_implementation` in the same slot). `intakeSchema`, `terminalNodes`, `defaultBudget`, and `policy: deterministic` are byte-identical to `@0.3.0`.

**Rationale:**
The agent YAML is the contract between FlowResolver and the executor registry. It must exist before T-299 can wire bootstrap (which loads the agent definition to enumerate nodes for `validate_executor_coverage`). YAML lands first; bindings follow.

**Acceptance Criteria:**
- [ ] File `agents/lifecycle-agent@0.4.0-manual.yaml` exists with `ref: lifecycle-agent@0.4.0-manual` and `version: "0.4.0-manual"`.
- [ ] `nodes` list contains: `start`, `load_work_item`, `confirm_brief`, `register_work_item`, `generate_tasks`, `confirm_tasks`, `propose_tasks`, `assign_task`, `generate_plan`, `confirm_plan`, `approve_plan`, `request_implementation`, `submit_implementation`, `human_review_implementation`, `approve_review`, `mark_task_done`, `correct_implementation`, `close_work_item`, `terminate_correction_budget`. (No `review_implementation`.)
- [ ] `flow.transitions` matches the diff in FEAT-015 §4.1: four inserted edges (`load_work_item → confirm_brief → register_work_item`, etc.), one substituted branch entry (`human_review_implementation` carries the `review_passed` branch).
- [ ] The agent loader (`agents/loader.py`) parses the file without error; `AgentDefinition.flow.transitions` matches the YAML structure.
- [ ] `intakeSchema` is byte-identical to `@0.3.0` (`anyOf: [workItem, workItemPath]`).
- [ ] `terminalNodes: [close_work_item, terminate_correction_budget]` unchanged.

**Files to Modify/Create:**
- `agents/lifecycle-agent@0.4.0-manual.yaml` — new file.

**Technical Notes:**
- Each new node's `description` block matches the descriptions in FEAT-015 §4.1's YAML diff. The `inputSchema` for each is minimal — `taskId` required only on `confirm_plan` and `human_review_implementation` (per the signal contracts in §7).
- The header comment block at the top should mirror the `@0.3.0` header style, calling out which sibling variant this is and pointing at FEAT-015.
- `validate_executor_coverage()` at lifespan startup will fail if T-299 isn't shipped — that's expected; both must merge together.

---

### T-299: `register_lifecycle_v04_manual` bootstrap + lifespan wiring

**Type:** Backend
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-295, T-296, T-297, T-298

**Description:**
Add `register_lifecycle_v04_manual(...)` to `src/app/modules/ai/executors/bootstrap.py`. The function: (a) calls `register_lifecycle_v03(registry, ..., agent_ref="lifecycle-agent@0.4.0-manual", skip_review_implementation=True)` to install every shared binding under the new ref except the LLM reviewer; (b) registers four `HumanExecutor` bindings for the new checkpoint nodes, each with its matching `memory_patch_builder` from T-297. Wire the new helper from `src/app/lifespan.py` alongside the existing `register_lifecycle_v03` call.

**Rationale:**
This is the integration point. Once it lands plus T-298 plus T-296/T-297, the agent is bootable end-to-end. The single helper is the source of truth for variant coverage — `validate_executor_coverage` walks every node in `@0.4.0-manual` and confirms a binding exists.

**Acceptance Criteria:**
- [ ] `register_lifecycle_v04_manual(registry, lifecycle_client, session_factory, work_item_workflow_id, task_workflow_id, actor)` registers exactly 19 bindings for `agent_ref="lifecycle-agent@0.4.0-manual"`: 14 shared with v0.3.0 + 4 new human checkpoints + 1 human reviewer in place of the LLM one.
- [ ] Each of the four new human checkpoint bindings carries its `expected_signal_name` and `memory_patch_builder`:
  - `confirm_brief` → `brief-confirmed`, builder `_apply_brief_correction`.
  - `confirm_tasks` → `tasks-confirmed`, builder `_apply_tasks_correction`.
  - `confirm_plan` → `plan-confirmed`, builder `_apply_plan_correction`.
  - `human_review_implementation` → `review-completed`, builder `_apply_review_verdict`.
- [ ] `src/app/lifespan.py` invokes `register_lifecycle_v04_manual` after `register_lifecycle_v03`, sharing the same `lifecycle_client` / `session_factory` / workflow IDs.
- [ ] `validate_executor_coverage()` at lifespan startup passes for both agent refs (boot succeeds).
- [ ] Starting a run with `agentRef: "lifecycle-agent@0.4.0-manual"` via `POST /api/v1/runs` returns 202 and parks at `confirm_brief` (smoke check; full end-to-end is T-302).
- [ ] No new top-level import of `core.llm` in this module or its dependencies (verified by the existing `tests/test_runtime_deterministic_is_pure.py` guard — the four new bindings are `HumanExecutor`, not `LLMContentExecutor`).

**Files to Modify/Create:**
- `src/app/modules/ai/executors/bootstrap.py` — new helper function (~30 lines).
- `src/app/lifespan.py` — one new line in the executor-registration block.

**Technical Notes:**
- `register_lifecycle_v03` (after T-295) is the workhorse — most of the v0.4.0-manual function body is one call plus four `registry.register` lines. The total addition should be under 40 lines.
- The four new bindings have no `timeout_seconds` set — they inherit the unbounded-human-wait default from the runtime fix landed in PR #86.
- The `actor` parameter (default `"lifecycle-agent"`) is reused for both variants; no need for a per-variant actor today.

---

## Testing

### T-300: Unit tests for the four memory-patch builders

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-297

**Description:**
Create `tests/modules/ai/executors/test_lifecycle_manual_patches.py`. For each of the four builders, cover: (1) empty payload → empty patch; (2) full valid payload → expected nested patch shape; (3) malformed payload (Pydantic validation fails) → exception; (4) builder-specific business rules (e.g., empty tasks list, missing `current_task_id` for plan, invalid verdict).

**Rationale:**
The builders are the load-bearing surface between operator signals and `RunMemory` state. Coverage at the unit level isolates the patch logic from the executor + runtime + DB layers — fast feedback, exhaustive shape testing.

**Acceptance Criteria:**
- [ ] For `BriefConfirmedPayload` / `_apply_brief_correction`:
  - Empty payload → `{}`.
  - `{workItem: {title: "X"}}` → `{"lifecycle.v1": {"work_item": {..., title: "X"}}}` with non-overridden fields preserved.
  - `{workItem: {type: "BAD_TYPE"}}` → Pydantic validation error.
- [ ] For `TasksConfirmedPayload` / `_apply_tasks_correction`:
  - Empty payload → `{}`.
  - `{tasks: [{id: "T-1", title: "X"}]}` → patch with that single task.
  - `{tasks: []}` → `ValueError`.
  - `{tasks: [{title: "X"}]}` (missing required `id`) → Pydantic validation error.
- [ ] For `PlanConfirmedPayload` / `_apply_plan_correction`:
  - Empty payload → `{}`.
  - `{plan: "# Updated"}` with `current_task_id="T-1"` → `{"lifecycle.v1": {"taskPlans": {"T-1": "# Updated"}}}`.
  - `{plan: "..."}` with `current_task_id=None` → `ValueError`.
- [ ] For `ReviewCompletedPayload` / `_apply_review_verdict`:
  - `{verdict: "pass"}` → patch with `reviewHistory[]` entry (`reviewer: "human"`, `verdict: "pass"`, `feedback: None`).
  - `{verdict: "fail", feedback: "needs work"}` → entry with `feedback: "needs work"`.
  - `{verdict: "needs_changes"}` → Pydantic validation error (literal mismatch).
  - Missing `verdict` → Pydantic validation error.
- [ ] All tests pass; `pyright` clean.

**Files to Modify/Create:**
- `tests/modules/ai/executors/test_lifecycle_manual_patches.py` — new file (~15-20 small tests).

**Technical Notes:**
- No DB / session needed — the builders are pure functions. Pass a `LifecycleMemory` Pydantic model directly where the builder needs read access (plan + review builders).
- Use `pytest.parametrize` for the per-builder happy-path cases to keep the file readable.

---

### T-301: Unit tests for `HumanExecutor.memory_patch_builder` hook

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-296

**Description:**
Extend the existing `HumanExecutor` test file (or create one if missing) with three tests: (1) executor with no `memory_patch_builder` returns the same envelope shape as today; (2) executor with a builder calls it once and embeds the result at `result.__memory_patch`; (3) executor with a builder that raises returns a `failed` envelope with the exception in `detail`.

**Rationale:**
T-300 covers the builder shape; this task covers the executor's plumbing. The two are complementary unit-test surfaces — if either regresses, the failure is local to one of them.

**Acceptance Criteria:**
- [ ] Test 1 (no builder): `result` equals `{**signal_payload}` (or whatever the no-builder shape is today — check the current contract).
- [ ] Test 2 (builder set): builder is called exactly once with the signal payload; `result["__memory_patch"]` equals the builder's return value.
- [ ] Test 3 (builder raises): envelope `state="failed"`, `outcome="error"`, `detail` contains the exception's type and message; the integration with the runtime's `raise _ExecutorFailure` pipeline is left to T-302 (the e2e test surfaces this through the run terminating with `stop_reason=error`).
- [ ] `pyright` clean.

**Files to Modify/Create:**
- `tests/modules/ai/executors/test_human_executor.py` (extend if exists; else create).

**Technical Notes:**
- Stub the supervisor — these are pure executor tests; the supervisor's `deliver_signal` path is exercised by the integration test (T-302).
- Builder tests inject a stub `lambda payload: {"k": "v"}` — no need to bring in the real builders here.

---

### T-302: End-to-end integration test for the manual variant

**Type:** Testing
**Workflow:** standard
**Complexity:** L
**Dependencies:** T-299, T-300, T-301

**Description:**
Create `tests/integration/test_lifecycle_v04_manual.py`. Run a full lifecycle run against `lifecycle-agent@0.4.0-manual` with a stubbed `FlowEngineLifecycleClient` (engine calls intercepted with `respx`), a stubbed LLM provider (`StubLLMProvider` for the LLM-content nodes), and scripted signal delivery for the four checkpoints. Assert each `paused` state transition, each signal-driven advance, the engine receives the *edited* task list (not the LLM original) after `confirm_tasks` edits, and the final run terminates at `close_work_item` with `RunStatus.COMPLETED`.

**Rationale:**
This is the cutover-proof test for FEAT-015. Every other task contributes a piece; this one demonstrates the pieces hang together. The "edited tasks reach the engine" assertion is the load-bearing claim of the manual variant — it proves §3 ("LLM proposes, operator disposes").

**Acceptance Criteria:**
- [ ] Test scenario: a brief with 3 LLM-generated tasks; operator edits the task list at `confirm_tasks` to 2 tasks; operator approves the plan unchanged at each `confirm_plan`; operator approves each implementation at `human_review_implementation`; run completes.
- [ ] Assertions on the journey:
  - Run.status flips: `pending → running → paused (confirm_brief) → running → ... → paused (confirm_tasks) → running → ... → completed`.
  - `propose_tasks` invokes `lifecycle_client.create_item` exactly 2 times (the edited count), not 3 (the LLM count).
  - For each of the 2 tasks: the run passes through `generate_plan → confirm_plan → approve_plan → request_implementation → submit_implementation → human_review_implementation → approve_review → mark_task_done`.
  - `LifecycleMemory.reviewHistory` contains 2 entries, each with `reviewer: "human"` and `verdict: "pass"`.
  - No `policy_calls` row with `provider != "stub"` exists for this run (the deterministic runtime never reaches `core.llm`).
- [ ] Optional bonus assertion: the run's `Dispatch` table shows 4 rows with `mode=human` and `state=completed`, one per checkpoint.
- [ ] Test runs against real Postgres (per CLAUDE.md) and real `RunSupervisor` — no DB mocks.
- [ ] Pyright clean.

**Files to Modify/Create:**
- `tests/integration/test_lifecycle_v04_manual.py` — new file.
- `tests/integration/conftest.py` (if needed) — extend the lifecycle-v0.3.0 fixtures to also register v0.4.0-manual at lifespan time.

**Technical Notes:**
- Follow the structure in any existing v0.3.0 acceptance test (e.g., `tests/integration/test_lifecycle_v03_acceptance.py` or the closest equivalent). The signal-delivery pattern is in `tests/integration/test_runtime_human_pause.py` — `supervisor.deliver_signal(...)` after waiting for `Run.status == paused`.
- The stub LLM provider scripts (one for `load_work_item`, one for `generate_tasks`, one for `generate_plan` × 2) need to be deterministic. Same pattern as v0.3.0 tests.
- The engine stub: `respx.mock(base_url=...)` with `POST /api/workflows/{id}/items` (T1 / W1 create_item) and `POST /api/items/{id}/transitions` (T2/T4/T5/T6/T7/T9/T10/W2/W4/W6). The `propose_tasks` fanout will hit `create_item` once per task in the edited list.
- This test is the slow one in the suite (likely 5-10s with real Postgres + asyncio). Mark it with no special marker — the existing pytest config handles it.

---

## Documentation

### T-303: `docs/api-spec.md` — document the four signal contracts

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-297 (schemas finalized)

**Description:**
Add a section to `docs/api-spec.md` documenting the four new signal names accepted by `POST /api/v1/runs/{runId}/signals`. For each: signal name, required `taskId` presence, payload schema (camelCase, with field types + optional/required markers), example request body, example response. Include a top-level note that these signals are accepted only when the run's `agent_ref` is `lifecycle-agent@0.4.0-manual` (the route still 202s for unrelated runs but the executor never consumes them — a stale-signal note worth calling out).

**Rationale:**
External callers (UI, CLI extensions, custom integrations) need a contract to write against. The Pydantic schemas in T-297 are the source of truth in code; this doc surfaces them for humans. Required per CLAUDE.md "Documentation Maintenance Discipline" — new endpoints or DTOs trigger api-spec.md updates.

**Acceptance Criteria:**
- [ ] Each of the four signals (`brief-confirmed`, `tasks-confirmed`, `plan-confirmed`, `review-completed`) has its own subsection with: name, required-fields, payload schema (table or YAML-like), 1 happy-path example, 1 edit example.
- [ ] The cross-reference table at the bottom of `api-spec.md` (if one exists) includes the new signal names with their owning agent ref.
- [ ] Changelog entry at the bottom of the document per CLAUDE.md ("Every update to `data-model.md`, `api-spec.md`, etc. must include a changelog entry").
- [ ] Markdown renders cleanly in GitHub's preview.

**Files to Modify/Create:**
- `docs/api-spec.md` — new section under "Signals" (or wherever existing signal docs live).

**Technical Notes:**
- Reuse the description text from FEAT-015 §4.1 — don't rewrite. The brief is the authoritative source; this doc is a structured restatement for API consumers.
- Avoid linking to specific code paths (they move). Link to the Feature Brief instead: `[FEAT-015](work-items/FEAT-015-lifecycle-manual-variant.md)`.

---

### T-304: `CLAUDE.md` + `docs/ARCHITECTURE.md` — variant pattern + Quick Reference

**Type:** Documentation
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-298, T-299

**Description:**
1. **`CLAUDE.md` Quick Reference**: add an example `orchestrator run` command line using `lifecycle-agent@0.4.0-manual`. Add a one-line entry under "Patterns to Follow" describing the variant naming convention (`@<major>.<minor>.<patch>-<variant-suffix>`).
2. **`docs/ARCHITECTURE.md`**: short subsection under the lifecycle-agent component documenting the `register_lifecycle_v0X_*` pattern — each variant is a bootstrap function registering bindings against a distinct `agent_ref`, sharing the underlying engine workflows and data model. List the currently-shipping variants (v0.3.0 LLM, v0.4.0-manual, future v0.4.0-auto).

**Rationale:**
Future contributors (and the AI assistant in future sessions) need a one-stop signpost that "lifecycle agent variants" are a thing and the pattern is consistent. Skipping this leaves the next variant author re-discovering the design from grep.

**Acceptance Criteria:**
- [ ] `CLAUDE.md` Quick Reference block includes a `lifecycle-agent@0.4.0-manual` example command.
- [ ] `CLAUDE.md` Patterns section (or sibling) includes the variant naming + bootstrap-function convention as a single-line pattern entry.
- [ ] `docs/ARCHITECTURE.md` has a subsection (1-2 paragraphs) explaining the variant pattern and listing live variants. Includes a changelog entry per CLAUDE.md.
- [ ] No anti-pattern needed (the variants are additive; the existing FEAT-009/010/011 anti-patterns already cover the failure modes).

**Files to Modify/Create:**
- `CLAUDE.md` — two small edits.
- `docs/ARCHITECTURE.md` — one new subsection + changelog entry.

**Technical Notes:**
- Keep the ARCHITECTURE.md addition short — 1-2 paragraphs. The Feature Brief carries the deep design; ARCHITECTURE.md just needs the pointer.
- No need to update `docs/data-model.md` (no schema change) or `docs/ui-specification.md` (no UI).

---

## Summary

**By type:**
- Backend / Refactor: 5 (T-295, T-296, T-297, T-298, T-299)
- Testing: 3 (T-300, T-301, T-302)
- Documentation: 2 (T-303, T-304)

**Complexity:**
- S: 6 (T-295, T-296, T-298, T-301, T-303, T-304)
- M: 3 (T-297, T-299, T-300)
- L: 1 (T-302)
- XL: 0

**Critical path:** T-295 → T-296 → T-297 → T-299 → T-302 (five steps). T-298 (YAML) can land in parallel with T-295/T-296/T-297. T-300 / T-301 / T-303 / T-304 fan off the critical path and gate the final merge.

**Workflow notes:**
- All tasks are `standard`. No `mockup-first` (no UI). No `investigation-first` (the design is already pinned in FEAT-015).
- Each task lists "Files to Modify/Create" so `plan-generation.md` can be run against any single task without further context-gathering.

**Risks / open questions discovered during analysis:**
- **Memory-patch shape for review history.** T-297 says the `_apply_review_verdict` builder must write the same `reviewHistory[]` entry shape the LLM `_patch_review` writes today. If the LLM path's shape isn't stable / well-tested as a contract, the human path could drift. Mitigation: T-297 and T-302 both pin the shape; T-302 asserts both human-written and (in a future cross-variant test) LLM-written entries have identical structure modulo `reviewer`.
- **Idempotent re-signals after a paused checkpoint.** Today's signal idempotency is `(run_id, name, task_id)`. For `brief-confirmed` / `tasks-confirmed` (no `taskId`), the dedupe key collapses to `(run_id, name)` — fine, but worth verifying the signal-create adapter handles the NULL `task_id` correctly. T-302's "deliver each signal exactly once" pattern proves the happy path; an explicit edge-case test for "deliver brief-confirmed twice" could land as a follow-up if time allows.
- **`current_task_id` resolution for `confirm_plan`'s memory patch.** The builder needs the current task id at signal-time. T-297 has the builder read it from a passed-in `LifecycleMemory`. If `current_task_id` is `None` (e.g., the operator delivers `plan-confirmed` before `generate_plan` has set it), the builder raises. This is correct but the resulting `failed` envelope terminates the run — operators can't easily recover. Worth surfacing in the operator-facing docs (T-303): "deliver `plan-confirmed` only after the run is parked at `confirm_plan`."
- **Boot-fail blast radius.** If T-299's helper is buggy, lifespan startup fails for *both* variants (the orchestrator refuses to boot). This is by design (`validate_executor_coverage` is the fail-fast gate), but worth noting for the rollout — staging-first before main.
