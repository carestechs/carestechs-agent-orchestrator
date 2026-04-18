# Implementation Plan: T-097 — Correction-bound enforcement in runtime + `final_state.reason`

## Task Reference
- **Task ID:** T-097
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-089, T-096

## Overview
Adds a per-task correction counter in lifecycle memory, increments it on entry to the `corrections` node, and terminates the run with `stop_reason=error` when any task exceeds `LIFECYCLE_MAX_CORRECTIONS` (default 2). The trip fires inside the existing `error` priority bucket and records `final_state.reason="correction_budget_exceeded"`.

## Steps

### 1. Modify `src/app/config.py`
Add field:
```python
lifecycle_max_corrections: int = Field(default=2, ge=1)
```
Env var binding: `LIFECYCLE_MAX_CORRECTIONS`.

### 2. Modify `src/app/modules/ai/stop_conditions.py`
Add a pure function:
```python
def correction_budget_exceeded(memory: LifecycleMemory, max_corrections: int) -> StopReason | None:
    for task_id, attempts in memory.correction_attempts.items():
        if attempts > max_corrections:
            return StopReason.ERROR
    return None
```
Extend `evaluate` to call this between `cancelled` and the existing error check, passing memory + settings. Preserve the documented priority order (`cancelled > error > budget_exceeded > policy_terminated > done_node`).

### 3. Modify `src/app/modules/ai/runtime.py`
Add an on-entry hook and call it when the selected node is `corrections`:
```python
def _on_node_entry(node_name: str, memory: LifecycleMemory) -> LifecycleMemory:
    if node_name != "corrections":
        return memory
    task_id = memory.current_task_id
    if task_id is None:
        return memory
    attempts = dict(memory.correction_attempts)
    attempts[task_id] = attempts.get(task_id, 0) + 1
    return memory.model_copy(update={"correction_attempts": attempts})
```
Invoke after the policy selects the node, before the tool handler runs. Then evaluate `correction_budget_exceeded` immediately; if it fires, call `_terminate(StopReason.ERROR, final_state={"reason": "correction_budget_exceeded", "task_id": ..., "attempts": ...})`.

Because memory here is `LifecycleMemory`-shaped but the runtime works on raw dicts, call `from_run_memory` / `to_run_memory` at the hook boundary. Alternative: keep the hook generic (operating on a `dict`) and move the lifecycle-specific increment logic to a lifecycle-agent-specific entry hook registered via agent definition. v1: inline in the runtime since there's one lifecycle agent — revisit when a second lifecycle-style agent lands.

### 4. Modify `tests/modules/ai/test_stop_conditions.py`
Add 3 cases:
- `test_corrections_under_bound` — `correction_attempts={"T-1": 2}` with max=2 → `None`.
- `test_corrections_over_bound` — `correction_attempts={"T-1": 3}` with max=2 → `StopReason.ERROR`.
- `test_priority_cancelled_beats_correction` — both conditions trip, `cancelled` wins.

### 5. Create `tests/integration/test_runtime_corrections.py`
One integration test — stub policy loops review(fail) → corrections three times; assert `stop_reason=error`, `final_state.reason="correction_budget_exceeded"`, `final_state.task_id="T-FIXTURE"`, `final_state.attempts=3`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/config.py` | Modify | New `lifecycle_max_corrections` setting. |
| `src/app/modules/ai/stop_conditions.py` | Modify | `correction_budget_exceeded` pure function; `evaluate` extension. |
| `src/app/modules/ai/runtime.py` | Modify | `_on_node_entry` + corrections-increment + termination path. |
| `tests/modules/ai/test_stop_conditions.py` | Modify | +3 cases. |
| `tests/integration/test_runtime_corrections.py` | Create | End-to-end integration. |

## Edge Cases & Risks
- **Counter increments on entry, not on exit**: the semantics is "how many times did the operator have to retry after a failed review." Incrementing on entering `corrections` (which is the node the policy routes to *after* a failed review) matches operator intuition — the first implementation attempt isn't a correction.
- **`current_task_id` unset**: if the `corrections` node is somehow entered before `current_task_id` is set (impossible given the flow-transitions map in T-100), the hook is a no-op. Defensive but never fires in practice.
- **Hook placement**: after policy selects node, before tool handler runs. This matters: if we incremented after the handler, a handler crash would swallow the increment.
- **Generic vs. lifecycle-specific hook**: inlining the lifecycle-specific logic into the runtime is a small AD-3 violation (the runtime "knows" about the lifecycle agent). Rationale: one consumer; scope creep to add a plugin hook system isn't warranted. Document the rough edge.
- **Coupling to `LifecycleMemory` shape**: the hook imports `LifecycleMemory`. Future non-lifecycle agents whose memory doesn't include `correction_attempts` will never trip this bound (the hook no-ops). Safe.

## Acceptance Verification
- [ ] `Settings.lifecycle_max_corrections` reads `LIFECYCLE_MAX_CORRECTIONS`, default 2.
- [ ] `correction_budget_exceeded` returns `StopReason.ERROR` when any task's attempts > max.
- [ ] Priority order preserved: `cancelled > error > budget_exceeded > policy_terminated > done_node`.
- [ ] `final_state.reason="correction_budget_exceeded"`, `final_state.task_id`, `final_state.attempts` populated on trip.
- [ ] `_on_node_entry` fires once per entry into `corrections`.
- [ ] 3 unit tests + 1 integration test pass.
- [ ] `uv run pyright` + `uv run ruff check .` clean.
