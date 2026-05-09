# Bug Report: BUG-013 — Multi-task work items only process the first task; remaining tasks orphan in the engine

> **Purpose**: Capture a structural gap in `lifecycle-agent@0.3.0`'s per-task loop. `LifecycleMemory.current_task_id` is set once (to the first task's id) by `_patch_generate_tasks` and **never advanced** by any v0.3.0 writer. As a consequence, multi-task work items run only the first task end-to-end; remaining tasks are created in the engine, partially planned, and then abandoned in a non-terminal state when the work item closes after the first task's `approve_review`. End-to-end tests don't exercise this path — both fixtures use a single task.

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | BUG-013 |
| **Summary** | The deterministic per-task loop in `lifecycle-agent@0.3.0` reads `LifecycleMemory.current_task_id` to address the engine task on every per-task node (`assign_task`, `generate_plan`, `approve_plan`, `request_implementation`, `submit_implementation`, `review_implementation`, `approve_review`). The id is written exactly once by `_patch_generate_tasks` and is never updated. The `unplanned_tasks_remaining` self-loop on `generate_plan` therefore re-plans the same task indefinitely (or, depending on whether the LLM elects to plan a different id than the one it was asked about, writes plan rows for tasks that the rest of the loop will never address). After plans complete, `approve_plan → request_implementation → … → approve_review → close_work_item` runs against `current_task_id` (the first task only); other tasks orphan in `plan_review` (engine-side) and the work item closes prematurely on `approve_review`'s unconditional transition to `close_work_item`. |
| **Severity** | High. v0.3.0 is the production lifecycle agent; any FEAT or BUG work item that decomposes into more than one task is currently un-shippable through the orchestrator. The system prompt for `generate_tasks` even says "A typical FEAT decomposes into 3–8 tasks" — precisely the case that doesn't work. |
| **Status** | Open |
| **Reported By** | Operator-led walk-through of the lifecycle (2026-05-09) |
| **Date Reported** | 2026-05-09 |
| **Date First Observed** | 2026-05-09 (gap exists since FEAT-011 / lifecycle-agent@0.3.0 shipped; never surfaced because the e2e tests are single-task) |
| **Related** | FEAT-011 (the v0.3.0 lifecycle port), BUG-004 (per-task wiring assumed `current_task_id` would advance), BUG-012 (sibling lossy-context bug at `generate_tasks`) |

---

## 2. Reproduction

1. Create a FEAT brief at `docs/work-items/FEAT-MULTI-TEST.md` with content rich enough that `generate_tasks` will produce ≥ 2 tasks (or stub the LLM to return a 2-task list).
2. Run: `uv run orchestrator run lifecycle-agent@0.3.0 --intake workItemPath=docs/work-items/FEAT-MULTI-TEST.md --follow`.
3. Provide an `implementation-complete` signal when the run pauses on `request_implementation`.
4. Allow `review_implementation` to return `verdict=pass`.

**Expected:** every task progresses through assign → plan → approve_plan → implement → review → approve_review. The work item only closes once all tasks are `done` on the engine.

**Actual (depending on LLM behaviour during the planning loop):**
- Either: `generate_plan` infinite-loops on the same `current_task_id` (predicted from a strict reading of the code: `_resolve_current_task_engine_id` → `find_current_task` → reads `current_task_id` → unchanged → resolver picks `generate_plan` again because `unplanned_tasks_remaining` is still True for the not-yet-planned tasks). Run trips the step budget eventually.
- Or: the `generate_plan` LLM is given the prompt for the first task but writes `plans[task_id]` keyed on a *different* task id (because the LLM may notice from memory that other tasks exist and try to be helpful). The self-loop terminates when every task has a `plans[*]` entry, but the implementation/review/approve_review chain still runs only against `current_task_id` — the first task. The work item closes; tasks 2..N stay in `plan_review` on the engine.

Both outcomes are wrong. The first is loud (run fails on budget); the second is silent (run reports `completed`, engine state lies).

---

## 3. Root Cause

### 3.1 `current_task_id` has exactly one writer

```bash
$ grep -rn "current_task_id\s*=" src/app/modules/ai/
src/app/modules/ai/executors/bootstrap.py:492:            current_task_id=(str(tasks_in[0].get("id")) if tasks_in else None),
src/app/modules/ai/tools/lifecycle/wait_for_implementation.py:48:    new_memory = memory.model_copy(update={"current_task_id": task_id})
```

`bootstrap.py:492` is `_patch_generate_tasks` — sets the id to the first task once. `wait_for_implementation.py` is a v0.1.0 LLM-policy-mode tool — never reached on the v0.3.0 path. So in v0.3.0: one writer, one write, never advanced.

### 3.2 Every per-task node addresses `current_task_id`

`bootstrap.py:547-559` defines `_resolve_current_task_engine_id`:

```python
async def _resolve_current_task_engine_id(ctx: DispatchContext) -> uuid.UUID | None:
    async with session_factory() as session:
        mem = await session.scalar(_select(_RunMemoryModel).where(_RunMemoryModel.run_id == ctx.run_id))
    memory = read_lifecycle_memory((mem.data if mem is not None else {}) or {})
    task = find_current_task(memory)  # ← reads memory.current_task_id
    if task is None or task.engine_item_id is None:
        return None
    ...
```

This is the `target_id_resolver` for **every** per-task `register_engine_executor` call: `assign_task` (T5), `generate_plan` (T6 inside the composite), `approve_plan` (T7), `submit_implementation` (T9 inside its custom executor), `approve_review` (T10). They all hit the same task — the first one.

### 3.3 The flow graph has no per-task fan-out at the implementation layer

`agents/lifecycle-agent@0.3.0.yaml:194-217`:

```yaml
generate_plan:
  branch:
    rule: unplanned_tasks_remaining
    "true": generate_plan        # ← self-loop covers planning fan-out (broken; see 3.4)
    "false": approve_plan
approve_plan: [request_implementation]
request_implementation: [submit_implementation]
submit_implementation: [review_implementation]
review_implementation:
  branch:
    rule: review_passed
    "true": approve_review
    "false": correct_implementation
approve_review: [close_work_item]   # ← unconditional close after one task's review
```

There is **no** predicate analogous to `unplanned_tasks_remaining` after `approve_review` to loop back to `assign_task` (or to `request_implementation`) for the next task. `approve_review → close_work_item` is an unconditional terminal hop.

### 3.4 The planning self-loop is also broken

Even within the planning phase that *does* attempt fan-out, the resolver's predicate (`flow_predicates.py:_unplanned_tasks_remaining`) returns True iff any task lacks an entry in top-level `plans[]`. But the executor for `generate_plan` always addresses `current_task_id` — which is fixed at the first task. So:
- Iteration 1: resolver picks `generate_plan`. Executor plans `T-001`. Patch builder writes `plans["T-001"]`. (Or: LLM is helpful and writes a different id; depends on wording it interprets.)
- Iteration 2: resolver checks `unplanned_tasks_remaining` — still True for `T-002`. Picks `generate_plan` again. Executor still addresses `current_task_id == "T-001"` — re-plans the same task.

There is no fan-out helper, no "advance to next unplanned task" sidecar — the executor and the predicate disagree about what counts as "the next task to plan."

### 3.5 Why this never tripped a test

`tests/integration/test_lifecycle_v03_end_to_end.py:491` and `:540` — both end-to-end test fixtures script `generate_tasks` to return a single-task list:

```python
"generate_tasks": [
    {"tasks": [{"id": "T-1", "title": "only task", "executor": "claude-code"}]}
],
```

Single-task is the only path covered. The single-task path is correct; multi-task was never green.

---

## 4. Proposed Fix (high level — to be detailed in a plan)

The fix has two interacting layers; both are needed.

### 4.1 Per-task advancement

Introduce a `next_task` advancement that picks the next unfinished task. Two reasonable shapes:

**(a) Implicit advancement in patch builders.** `_patch_generate_plan` advances `current_task_id` to the next task without a `plans[*]` entry; `approve_review`'s patch builder advances `current_task_id` to the next task whose engine state is not `done`. Pro: no graph changes. Con: spreads "what's the next task" logic across multiple patch builders; easy to drift.

**(b) An explicit `select_next_task` node.** A `LocalExecutor`-mode node that reads memory, picks the next task to address, writes `current_task_id`. Insert it before each per-task hop (or, more cleanly, only at the head of the loop). Pro: one writer, one source of truth. Con: graph-shape change and more nodes.

Recommend **(b)** — explicit nodes are easier to reason about and easier to test. Matches the FEAT-009 verb-imperative convention.

### 4.2 Loop-back from `approve_review`

Once a task reaches `done`, branch on "are there unfinished tasks remaining":
- True → back to `assign_task` (or `select_next_task` if (4.1b) is taken).
- False → `close_work_item`.

Add a `tasks_remaining` predicate to `flow_predicates.py` that checks for any task whose engine state is not `done`.

### 4.3 Engine-state read

The current memory shape doesn't track per-task engine state cleanly (only `engine_item_id`). The reactor's `tasks` cache (FEAT-008) does — the `Task.status` column is the reactor-managed cache of engine state. Predicates that need to know "is this task done" should read from the cache, not from memory. Worth confirming in the plan.

---

## 5. Out of Scope

- Parallel task execution. The fix is sequential per-task: finish one before starting the next. Parallel implementation would be a separate FEAT.
- Restart / recovery semantics for half-finished multi-task runs. The reconciler (`reconcile-aux`, `reconcile-dispatches`) already handles single-task orphans; multi-task adds new orphan shapes (e.g. tasks 2..N stuck in `plan_review`). A clean-up strategy is its own follow-up.
- Whether v0.1.0 (LLM-policy mode) has the same gap. v0.1.0 advances `current_task_id` via the `wait_for_implementation` tool (`wait_for_implementation.py:48`), which is reached on every iteration of its loop. That path is structurally different and not in scope here.

---

## 6. Verification (when fix lands)

- New e2e test under `tests/integration/test_lifecycle_v03_multi_task.py` that scripts a 3-task `generate_tasks` and asserts:
  1. Three plan entries written, each keyed by its own task id.
  2. Three `request_implementation` pauses, each carrying a distinct `taskId` in the dispatch intake.
  3. Three `approve_review` engine calls, each against a distinct task `engine_item_id`.
  4. `close_work_item` fires exactly once, only after every task has reached `done`.
  5. Trace contains exactly one step per per-task node × N tasks (no duplicate plans for the same task; no skipped implementations).
- The single-task e2e test paths (`test_lifecycle_v03_end_to_end.py` happy-path and rejection-path) remain green unchanged.
- `unplanned_tasks_remaining` retains its current semantics; the new `tasks_remaining` predicate covers the implementation-phase loop.
