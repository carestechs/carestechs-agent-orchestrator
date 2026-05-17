# IMP-004 — Task Breakdown

> **Source brief:** `docs/work-items/IMP-004-human-assignment-checkpoint-manual-variant.md`
> **Numbering:** Continues from FEAT-015 (last task T-304).
> **Total tasks:** 6
> **Critical path:** T-305 → T-306 → T-307 → T-309 (4-step chain)

This IMP inserts a fifth human checkpoint (`confirm_assignment`) into the manual lifecycle variant. The shape mirrors the four existing checkpoints byte-for-byte: a new `HumanExecutor` binding with a memory-patch builder, a new signal contract, and one new node in the agent YAML. No new executor type, no schema migration, no runtime-loop change.

---

## Phase 0: Preparation

(Coverage is already in place from FEAT-015 / T-302 — the manual-variant integration test exercises the four existing checkpoints end-to-end and is the structural baseline this IMP extends. No new coverage task before implementation begins; new coverage lands in Phase 3 as part of the additions, not as a precondition.)

---

## Phase 1: Parallel Implementation

### T-305: Add `AssignmentConfirmedPayload` schema + `apply_assignment_confirmation` builder

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** None

**Description:**
Extend `src/app/modules/ai/executors/lifecycle_manual_patches.py` with a fifth signal payload schema and memory-patch builder, symmetric with the four existing ones. The schema is `AssignmentConfirmedPayload` with required `assignee: str` (non-empty) and optional `task_id: str | None` (camelCase JSON alias `taskId`). The builder `apply_assignment_confirmation(payload, *, lifecycle_memory) -> dict[str, Any]` resolves the target task id (from `payload.task_id` if present, else `lifecycle_memory.current_task_id`) and returns `{"assignments": {task_id: assignee}}` — a **top-level sidecar** matching the `plans` sidecar pattern already used by `apply_plan_correction`. When the target task id cannot be resolved, the builder raises `ValueError("assignment-confirmed received with no resolvable task id")`. Empty/whitespace-only `assignee` is rejected at schema level via a `field_validator`.

**Rationale:**
The four current checkpoints diverge only in payload shape and target memory key (IMP-004 §4). The plan sidecar comment in `lifecycle_manual_patches.py` (lines 17-19) explicitly documents that engine-aux sidecars sit beside `lifecycle.v1`, not nested — `assignments` follows the same shape, keyed by engine task id (BUG-013 uses `current_task_id` as the engine item id once `register_work_item` runs). Sidecar choice keeps `LifecycleTask` byte-identical to v0.3.0 so the "variants are peers" pattern (CLAUDE.md) holds — v0.3.0 memory never grows an `assignments` key.

**Acceptance Criteria:**
- [ ] `AssignmentConfirmedPayload(assignee="alice")` validates; `assignee=""` and `assignee="   "` raise `ValidationError`.
- [ ] `AssignmentConfirmedPayload(assignee="alice", taskId="t-1")` validates (camelCase alias) and exposes `payload.task_id == "t-1"`.
- [ ] `apply_assignment_confirmation(payload, lifecycle_memory=mem)` with `payload.task_id="t-1"` returns `{"assignments": {"t-1": "alice"}}`.
- [ ] When `payload.task_id is None` and `lifecycle_memory.current_task_id == "t-2"`, builder returns `{"assignments": {"t-2": "alice"}}`.
- [ ] When `payload.task_id is None` and `lifecycle_memory.current_task_id is None`, builder raises `ValueError`.
- [ ] Patch shape merges shallowly: builder is a pure function, returns a fresh dict, never mutates `lifecycle_memory`.
- [ ] `pyright` clean; module export list updated.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/lifecycle_manual_patches.py` — append schema + builder; export from module-level `__all__`.
- `src/app/modules/ai/tools/lifecycle/memory.py` — **no change**. Sidecar lives outside the `LifecycleMemory` Pydantic model exactly like `plans`.

**Technical Notes:**
- The builder must NOT shallow-merge against existing `assignments` from memory and overwrite — at this point in the flow `current_task_id` is the only task being assigned, and the runtime's `_write_state` shallow-merges top-level keys (per `_patch_generate_plan`'s precedent BUG-013 fix). To preserve prior assignees on loop-back, the builder must read the existing `assignments` blob from `lifecycle_memory` (via a raw passthrough — there is no `LifecycleMemory` field for it) and merge in the new entry. Mirror the pattern in `_patch_generate_plan` which merges `merged_plans = dict(current_memory.get("plans") or {})` before writing.
- `lifecycle_memory` is passed in already validated; `current_task_id` may legitimately be None mid-flow before `propose_tasks` runs. The signal can only arrive *after* `propose_tasks`, but defending against None is cheap and surfaces a bug class clearly.

---

### T-306: Wire `confirm_assignment` HumanExecutor binding into `register_lifecycle_v04_manual`

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-305

**Description:**
Add a fifth `registry.register(...)` call in `register_lifecycle_v04_manual` (`src/app/modules/ai/executors/bootstrap.py`) for node name `confirm_assignment`, signal name `assignment-confirmed`, builder `apply_assignment_confirmation`. The binding is inserted alongside the existing four human checkpoints in the same section (between `confirm_plan` and the human reviewer). Update the `register_lifecycle_v04_manual` log line to say `5 human checkpoints + human reviewer`. Update the function's docstring accordingly.

**Rationale:**
Symmetric extension — the binding is one block of the same shape as the four existing checkpoints. Placement next to `confirm_plan` keeps the four pre-engine-commit checkpoints visually grouped (brief / tasks / assignment / plan); the human reviewer stays at the bottom of the function because it replaces an LLM node rather than gating one.

**Acceptance Criteria:**
- [ ] `register_lifecycle_v04_manual` registers an executor for `(agent_ref, "confirm_assignment")` of type `HumanExecutor` with `expected_signal_name="assignment-confirmed"` and `memory_patch_builder=apply_assignment_confirmation`.
- [ ] Function docstring lists the five checkpoints.
- [ ] Log line shows `5 human checkpoints`.
- [ ] Importing `apply_assignment_confirmation` from `lifecycle_manual_patches` succeeds at module load (catches T-305 export miss).
- [ ] Existing four checkpoints are unchanged byte-for-byte.

**Files to Modify/Create:**
- `src/app/modules/ai/executors/bootstrap.py` — add the import in the local import block (line ~1144) and the `registry.register(...)` block.

**Technical Notes:**
- Do not refactor the four existing checkpoint blocks into a loop "for elegance". Each binding is explicit by design — a loop would obscure the symmetry that a future sixth checkpoint also needs to follow.
- `register_lifecycle_v03` is **not** touched. The shared helper stays unaware of assignment because v0.3.0 has no human checkpoint there.

---

### T-307: Insert `confirm_assignment` node in `lifecycle-agent@0.4.0-manual.yaml`

**Type:** Backend
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-306 (so the executor exists when the YAML loads)

**Description:**
Edit `agents/lifecycle-agent@0.4.0-manual.yaml` to declare a new node `confirm_assignment` between `propose_tasks` and `assign_task`, and update the `flow.transitions` block accordingly. The node's `inputSchema` is the empty object pattern used by all `confirm_*` nodes. The header comment block at the top of the file is updated to mention five signals; the signal contracts comment lists `assignment-confirmed`.

**Rationale:**
The YAML is the agent's source of truth for the flow graph. The executor coverage validator (`validate_executor_coverage`) at lifespan startup confirms the node has a binding (T-306 guarantees this); a YAML-without-binding boot would refuse to start.

**Acceptance Criteria:**
- [ ] `nodes:` list includes `confirm_assignment` with a comment matching the style of the four existing `confirm_*` nodes (3-5 lines describing pause behavior + signal name + payload shape + memory write).
- [ ] `flow.transitions` block reads:
      ```
      propose_tasks: [confirm_assignment]
      confirm_assignment: [assign_task]
      assign_task: [generate_plan]
      ```
- [ ] Top-of-file comment block lists `assignment-confirmed` in the Signal contracts section.
- [ ] No other node, transition, intake schema, terminal list, or budget value is changed.
- [ ] `uv run uvicorn app.main:app` boots cleanly (executor coverage validates).
- [ ] `agents/lifecycle-agent@0.3.0.yaml` and `agents/lifecycle-agent@0.2.0.yaml` are byte-unchanged.

**Files to Modify/Create:**
- `agents/lifecycle-agent@0.4.0-manual.yaml` — additive node + transition edit.

**Technical Notes:**
- The transition list change touches three lines: `propose_tasks`'s target, the new `confirm_assignment` entry, and `assign_task`'s entry remains as-is. Do not reorder the transition block; insert in declaration order.
- The loop-back from `mark_task_done` already routes to `assign_task` — with this change, the loop-back transparently passes through `confirm_assignment` for each subsequent task. That's the intended per-task semantics (IMP-004 §4).

---

## Phase 2: Migration

(No migration tasks. The agent is opt-in via `agent_ref`; in-flight runs continue on whatever ref they started with. There is no "old v0.4.0-manual without assignment" to migrate off — the variant is new enough that no production run has reached `assign_task` without the checkpoint.)

---

## Phase 3: Verification

### T-308: Unit tests for `AssignmentConfirmedPayload` + `apply_assignment_confirmation`

**Type:** Testing
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-305

**Description:**
Add unit tests under `tests/modules/ai/executors/` (next to the existing `test_lifecycle_manual_patches.py` or equivalent) covering: schema validation (empty string, whitespace-only, camelCase alias roundtrip, extra-field rejection), builder happy path (with and without `payload.task_id`), builder error path (no resolvable task id), and the merge-with-existing-assignments behavior (loop-back preserves prior task assignees). Mirror the test layout of the four sibling builders.

**Rationale:**
The builder is a pure function — high-leverage unit tests at this layer mean the integration test in T-309 only has to exercise wiring, not shape semantics.

**Acceptance Criteria:**
- [ ] Tests cover all six AC bullets in T-305.
- [ ] One test specifically asserts the merge-preserve behavior: a memory with `{"assignments": {"t-1": "alice"}}` plus an `assignment-confirmed` for `t-2 → bob` produces `{"assignments": {"t-1": "alice", "t-2": "bob"}}`.
- [ ] All tests pass with `uv run pytest tests/modules/ai/executors/`.
- [ ] No real LLM, no real engine, no DB — pure-function tests only.

**Files to Modify/Create:**
- `tests/modules/ai/executors/test_lifecycle_manual_patches.py` — append tests for the new builder. If a fixture for `LifecycleMemory` already exists in the file, reuse it.

---

### T-309: Integration test — end-to-end `confirm_assignment` pause/resume + multi-task loop-back

**Type:** Testing
**Workflow:** standard
**Complexity:** M
**Dependencies:** T-306, T-307, T-308

**Description:**
Extend the existing FEAT-015 / T-302 manual-variant integration test (`tests/integration/test_lifecycle_v04_manual.py` or whatever file owns the v0.4.0-manual lifecycle test) with assertions that:

1. After `propose_tasks` completes, the run pauses at `confirm_assignment` (not `assign_task`); `Run.status == 'paused'` (per IMP-002).
2. `POST /api/v1/runs/{id}/signals` with `name=assignment-confirmed` and `payload.assignee="alice"` resumes the run; `Run.status` returns to `running`; `LifecycleMemory.<raw>["assignments"][<task1_id>] == "alice"`.
3. The next dispatched node is `assign_task` and T5 fires immediately after resume (assert via the engine stub recording one `task.T5` call for `task1_id`).
4. On a two-task work item, the loop-back from `mark_task_done` re-enters `confirm_assignment` for `task2_id`; second `assignment-confirmed` (with explicit `taskId=task2_id` to exercise the override path) advances the run; final `assignments` blob carries both `task1_id` and `task2_id`.
5. The run completes via `close_work_item` with both tasks at `done` in the engine stub.

The test reuses the engine stub, LLM stub, and CLI driver that T-302 already wires.

**Rationale:**
This is the closure test — the unit tests in T-308 prove the builder works, the integration test proves the runtime wiring + YAML + binding + signal route all line up. AC items 1-5 map 1:1 to IMP-004 §9 success criteria.

**Acceptance Criteria:**
- [ ] Test runs against a real Postgres (per CLAUDE.md testing convention) with stubbed LLM + engine.
- [ ] All five behavioral assertions above pass.
- [ ] Test fails if `confirm_assignment` is removed from the agent YAML.
- [ ] Test fails if T-306's binding is dropped.
- [ ] Test fails if the merge-with-existing assignments logic from T-305 is replaced with a naive overwrite (assertion 4 catches this).
- [ ] No timeout-tolerance hacks — the human-pause path completes in well under the dispatch timeout because the signal is delivered immediately by the test driver.

**Files to Modify/Create:**
- `tests/integration/test_lifecycle_v04_manual.py` — extend the existing test or add a new parametrized case for the two-task flow. If the existing test is single-task, prefer adding a sibling test rather than overloading the original (keep T-302's regression surface untouched).

---

### T-310: Update `docs/api-spec.md` + CLAUDE.md variants paragraph

**Type:** Docs
**Workflow:** standard
**Complexity:** S
**Dependencies:** T-306

**Description:**
Add `assignment-confirmed` to the signal contract table in `docs/api-spec.md` alongside `brief-confirmed` / `tasks-confirmed` / `plan-confirmed` / `review-completed`. Document payload shape, idempotency key (`(run_id, name="assignment-confirmed", task_id)`), and which agent variant accepts it (`lifecycle-agent@0.4.0-manual` only). Append a changelog entry per the project's documentation discipline. Update the "Lifecycle agent variants are peers" paragraph in `CLAUDE.md` to mention that the manual variant carries an `assignments` top-level sidecar that v0.3.0 does not.

**Rationale:**
Doc-first discipline — IMP-004 §11 lists `docs/api-spec.md` as the contract surface. Future operators (and future agents) need the signal documented in the same place they'd look for `plan-confirmed`.

**Acceptance Criteria:**
- [ ] `docs/api-spec.md` signal table row exists for `assignment-confirmed` with the same column layout as the four sibling rows.
- [ ] Changelog entry appended at the bottom of `docs/api-spec.md`.
- [ ] `CLAUDE.md` "Lifecycle agent variants are peers" paragraph mentions the variant-specific `assignments` sidecar.
- [ ] No edits to `docs/data-model.md` (LifecycleMemory is not a DB entity; the assignments sidecar lives in `RunMemory.data` JSON).

**Files to Modify/Create:**
- `docs/api-spec.md` — table entry + changelog line.
- `CLAUDE.md` — one paragraph edit in the manual-variant section.

---

## Summary

| Phase | Tasks | Notes |
|-------|-------|-------|
| 0 — Preparation | — | Existing FEAT-015 / T-302 coverage is the baseline; no new precondition task. |
| 1 — Implementation | T-305, T-306, T-307 | Schema + builder → binding → YAML node. Critical path. |
| 2 — Migration | — | No in-flight v0.4.0-manual runs to migrate; opt-in via `agent_ref`. |
| 3 — Verification | T-308, T-309, T-310 | Unit + integration + docs. |

**Critical path:** T-305 → T-306 → T-307 → T-309 (4 steps). T-308 can run parallel with T-306/T-307; T-310 parallel with T-309.

**Risk assessment:** Low. The change is one new node + one new binding + one new builder, all following established patterns. The only non-trivial decision (sidecar vs. nested field) is locked by precedent in `_patch_generate_plan`.

**Recommended review points:**
- After T-305: review the merge-with-existing-assignments logic — easy to get wrong.
- After T-307: smoke-boot the orchestrator to confirm executor coverage passes.
- After T-309: run the full `tests/integration/` suite to confirm no FEAT-015 regression.

**Rollback strategy:** Revert four files (`lifecycle_manual_patches.py`, `bootstrap.py`, the YAML, the new tests). No data migration. Any in-flight run paused at `confirm_assignment` would be cancelled via `POST /api/v1/runs/{id}/cancel`.
