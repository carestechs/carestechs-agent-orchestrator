# Bug Report: BUG-009 — Memory-shape mismatch in signal endpoint reader and `unplanned_tasks_remaining` predicate

> **Purpose**: Two readers in the orchestrator looked at the wrong memory location for the lifecycle task list — both pre-date the `lifecycle.v1` namespace introduced by FEAT-011 and were never migrated. The signal endpoint silently 404'd every operator signal under v0.3.0; the predicate silently misrouted the planning loop on multi-task work items. Filed and resolved in the same PR.

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | BUG-009 |
| **Summary** | `service.deliver_signal_for_run` and `flow_predicates._unplanned_tasks_remaining` both read top-level `RunMemory.data["tasks"]` — a sidecar slot that was either absent (signal endpoint) or only populated for already-planned tasks (predicate). Canonical task list lives at `data["lifecycle.v1"]["tasks"]` (a `list[LifecycleTask]`) since FEAT-011. |
| **Severity** | High (blocked operator signal delivery for every v0.3.0 run; the predicate misrouted multi-task planning loops, masked by single-task work items) |
| **Status** | Resolved |
| **Reported By** | Live `lifecycle-agent@0.3.0` run on 2026-05-02 — every `POST /api/v1/runs/{id}/signals` returned 404 after IMP-002 unblocked the human pause. Predicate bug surfaced during root-cause investigation of the same run. |
| **Date Reported** | 2026-05-02 |
| **Related** | FEAT-011 (introduced the `lifecycle.v1` namespace; left two readers behind), IMP-002 (the human-pause activation that exposed the signal-endpoint bug) |

---

## 2. Steps to Reproduce

**Signal endpoint:**

1. Start `lifecycle-agent@0.3.0` and let it advance to `request_implementation` (parks on a `HumanExecutor`, run goes to `paused` after IMP-002).
2. `POST /api/v1/runs/{id}/signals` with `{"name": "implementation-complete", "taskId": "T-001"}`.
3. **Observe:** 404 `task not found in run: T-001`. Run stays paused; eventually times out at the dispatch deadline.

**Predicate (`unplanned_tasks_remaining`):**

1. Run a hypothetical multi-task work item that generates `[T-001, T-002]` at `generate_tasks`.
2. After `generate_plan` completes for `T-001`, the resolver evaluates `unplanned_tasks_remaining` to decide whether to loop back to `generate_plan` for `T-002` or proceed to `approve_plan`.
3. **Observe:** predicate returns `False` even though `T-002` has no plan; flow proceeds to `approve_plan` and `T-002` is never planned.

(In practice the lifecycle has been exercised single-task only, so this bug went undetected; the multi-task fanout was a latent crash waiting to happen.)

---

## 3. Root Cause

Two readers were never migrated when FEAT-011 / T-255 moved the typed lifecycle shape from the top level of `RunMemory.data` into the `lifecycle.v1` namespace:

### Reader 1 — `src/app/modules/ai/service.py:294-303` (signal endpoint)

```python
tasks_raw: Any = memory_data.get("tasks") or []
known_task_ids: set[str] = set()
if isinstance(tasks_raw, list):
    for t in tasks_raw:
        ...
```

Looked at `data["tasks"]` (top-level) and only iterated when it was a `list`. Under v0.3.0 that key is either absent (most of the run) or a `dict` written by `_patch_generate_plan` (`{task_id: {}}` stub) — the `isinstance(..., list)` guard was False either way; `known_task_ids` stayed empty; every signal 404'd.

### Reader 2 — `src/app/modules/ai/flow_predicates.py:57-59` (`unplanned_tasks_remaining`)

```python
tasks = memory.get("tasks") or {}
plans = memory.get("plans") or {}
return any(task_id not in plans for task_id in tasks)
```

The only writer of top-level `data["tasks"]` was `_patch_generate_plan`, which writes `{task_id: {}}` for the *just-planned* task at the same time it writes `plans[task_id] = {plan_markdown}`. So `tasks` and `plans` were always populated together, in lock-step, for the same set of task IDs — `any(task_id not in plans for task_id in tasks)` always evaluated False. Single-task items got the right outcome by coincidence (the loop should exit anyway); multi-task items would silently skip un-planned tasks.

### Why the writer existed

`_patch_generate_plan` (`bootstrap.py:551`) wrote `"tasks": {task_id: {}}` alongside `"plans": {task_id: {...}}`. The empty dict was effectively a sidecar set used only by the broken predicate. Removing it has no other consumer.

---

## 4. Fix

Three coordinated changes:

1. **Signal endpoint reader (`service.py`):** use `read_lifecycle_memory(memory_data)` and pull task IDs from the typed model. Same accessor every other lifecycle reader uses.

2. **Predicate (`flow_predicates.py`):** read tasks via `read_lifecycle_memory(memory)` against the unchanged top-level `plans` dict. The predicate now reflects "any task in the canonical list lacks a plan entry."

3. **Writer (`bootstrap.py`):** drop `"tasks": {task_id: {}}` from `_patch_generate_plan`. The sidecar had no other consumer and was the source of the predicate's lock-step always-False behaviour.

```python
# service.py
from app.modules.ai.tools.lifecycle.memory import read_lifecycle_memory
lifecycle_memory = read_lifecycle_memory(memory_data)
known_task_ids: set[str] = {task.id for task in lifecycle_memory.tasks}

# flow_predicates.py
from app.modules.ai.tools.lifecycle.memory import read_lifecycle_memory
lifecycle_memory = read_lifecycle_memory(memory)
plans = cast(Mapping[str, Any], memory.get("plans") or {})
return any(task.id not in plans for task in lifecycle_memory.tasks)

# bootstrap.py — _patch_generate_plan
return {
    "plans": {task_id: {"plan_markdown": result.get("plan_markdown")}},
    # "tasks": {task_id: {}}  — dropped: sidecar only ever read by the
    # broken predicate; canonical list lives at lifecycle.v1.tasks.
}
```

---

## 5. Verification

- **`tests/modules/ai/test_routes_signals.py`** — new `TestNamespacedMemoryShape` class with two cases: v0.3.0-shaped memory accepts a known task (regression for the live 404), and unknown tasks under the same shape still 404. Existing v0.1.0 top-level shape tests unchanged and still pass — `read_lifecycle_memory`'s top-level fallback handles the legacy seed.
- **`tests/modules/ai/test_flow_predicates_lifecycle.py`** — new `TestUnplannedTasksRemaining` class covering: registered, no-tasks (False), all-planned (False), some-unplanned (True — the bug case), no-plans (True).
- **`tests/modules/ai/test_flow_resolver.py`** — `TestPredicateBranch` rewritten to seed the canonical `lifecycle.v1` shape via a helper. Pre-fix the test seeded the broken sidecar shape and codified the always-False behaviour; post-fix the assertions reflect real planning-loop semantics.
- **`tests/modules/ai/test_lifecycle_v03_branch_walk.py`** — `_BRANCH_SCENARIOS["generate_plan"]` updated to use the canonical shape via a new `_lifecycle_memory` helper. Same reason — was codifying the broken sidecar shape.
- Full suite: 1163 passed, 12 skipped (4 net new tests, 2 rewritten — same coverage scope, correct semantics).
- Live re-run will exercise the signal endpoint against the canonical memory shape.

---

## 6. Out of Scope

- **`read_lifecycle_memory`'s v0.1.0 fallback path is brittle when the top-level dict carries non-`LifecycleMemory` keys** (e.g. `plans`, `__feat009`) — `extra="forbid"` makes `model_validate` fail and the helper returns empty. No live caller hits this combination today (v0.1.0 didn't write sidecars; v0.3.0 writes the namespace), so leave it for a follow-on if it ever bites.
- **Memory-shape audit for other readers.** This fix addresses the two readers that bit. There may be other dormant readers still pointing at the v0.1.0 shape; leave them until they bite.

---

## Changelog

- 2026-05-02 — Filed and resolved in the same PR; two readers migrated to `read_lifecycle_memory`, one dead writer removed, four test seeds corrected to the canonical shape.
