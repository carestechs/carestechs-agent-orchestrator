# Implementation Plan: T-169 — Demote `work_items.status` + `tasks.status` to reactor-managed cache

## Task Reference
- **Task ID:** T-169
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** AC-9. Closes the "engine is sole writer" loop for the columns that matter most. Under engine-authority, `status` is a cache of the engine's truth, not a source of truth the orchestrator mutates.

## Overview
Transition functions in `work_items.py` / `tasks.py` stop writing `status`. The reactor writes it from the `item.transitioned` webhook payload. Under engine-absent, transition functions still write inline (dev-mode fallback). DTO reads are unchanged — callers see the cache; the cache is populated before any assertion via `await_reactor` in tests.

## Implementation Steps

### Step 1: Survey `status` writes
**File:** `src/app/modules/ai/lifecycle/work_items.py`, `tasks.py`
**Action:** Modify (survey first)

Find every `<entity>.status = ...` line. There will be one per transition function. Confirm the full set before editing.

### Step 2: Add a reactor-managed cache write path
**File:** `src/app/modules/ai/lifecycle/reactor.py`
**Action:** Modify

In `handle_transition`, after `_materialize_aux` (T-167), write the cache:

```python
async def handle_transition(db, webhook_event):
    correlation_id = webhook_event.correlation_id
    if correlation_id is not None:
        await _materialize_aux(db, correlation_id)

    # NEW: update the status cache
    await _update_status_cache(db, webhook_event)

    # ... existing derivation dispatch ...
    # ... effector dispatch from T-167 ...


async def _update_status_cache(db, webhook_event):
    entity_type = webhook_event.entity_type
    entity_id = webhook_event.entity_id  # the engine's item id → local uuid
    to_state = webhook_event.to_state

    if entity_type == "work_item":
        wi = await db.scalar(select(WorkItem).where(WorkItem.engine_item_id == entity_id))
        if wi is not None:
            wi.status = to_state
    elif entity_type == "task":
        task = await db.scalar(select(Task).where(Task.engine_item_id == entity_id))
        if task is not None:
            task.status = to_state
    await db.commit()
```

Assumes `engine_item_id` is a column on both (already added under FEAT-006 rc2). If lookup fails, log + skip — defensive but not fatal.

### Step 3: Strip status writes from transition functions
**File:** `src/app/modules/ai/lifecycle/work_items.py`
**Action:** Modify

For each transition function (`approve_task`, `defer_task`, `submit_plan`, etc.):

```python
# Before
async def submit_implementation(db, task_id, *, submitted_by, engine, correlation_id):
    task = await db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    _validate_transition(task.status, TaskStatus.IMPL_REVIEW)
    task.status = TaskStatus.IMPL_REVIEW.value  # ← this line goes away under engine-present
    if engine is not None:
        await engine.transition_item(...)
    return task


# After
async def submit_implementation(db, task_id, *, submitted_by, engine, correlation_id):
    task = await db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    _validate_transition(task.status, TaskStatus.IMPL_REVIEW)
    if engine is not None:
        await engine.transition_item(...)
    else:
        task.status = TaskStatus.IMPL_REVIEW.value  # engine-absent fallback
    return task
```

The validation still uses the local status (read-through cache). Write only happens in fallback.

**Concern:** between the signal adapter's commit and the reactor's status cache update, the local status is *stale*. The task row shows the prior state briefly. DTO reads during that window return stale status. Two options:

a) Accept it. `await_reactor` hides the window in tests; production consumers that care use the engine's read API directly (which doesn't exist yet).

b) Update the cache inline with the transition *validation check* so the signal's 202 response reflects the new state. But then the reactor's later update is redundant.

Pick (a) — accept the window. Engine-authority means the cache is eventually consistent; the tradeoff is explicit in the ADR.

### Step 4: Same treatment for tasks
**File:** `src/app/modules/ai/lifecycle/tasks.py`
**Action:** Modify

All task transition functions get the same engine-present/engine-absent conditional.

### Step 5: Derivations (W2, W5)
**File:** `src/app/modules/ai/lifecycle/work_items.py`, `reactor.py`
**Action:** Modify

Today's `maybe_advance_to_ready` and the W2 path mutate `work_item.status` directly. Under engine-authority, they call the engine's transition API; the reactor then updates the cache from the resulting webhook. That's a bigger change than a one-line swap — move `maybe_advance_to_ready` into the reactor (or into a module the reactor calls), and have it issue a transition call via the engine client.

If that's too invasive for this task, defer it: derivation functions stay inline-write under engine-present *and* engine-absent, with a FIXME comment to address in a follow-on. Document the gap.

Decision: **defer.** FEAT-008's scope says "effectors and aux writes through the reactor." Derivations are a separate architectural concern that this FEAT shouldn't swallow. T-172's integration test may surface this as a gap; if so, fix in a small follow-on task rather than bloating T-169.

### Step 6: Signal adapter — remove status writes
**File:** `src/app/modules/ai/lifecycle/service.py`
**Action:** Modify

Any adapter that bypasses the transition function and writes status directly needs the same treatment. There shouldn't be many — most go through the transition functions.

### Step 7: Unit test — signal adapter does not touch status when engine is present
**File:** `tests/modules/ai/lifecycle/test_reactor_status_cache.py`
**Action:** Create

```python
async def test_signal_does_not_mutate_status_under_engine_present(...):
    # Arrange: seed a task at "implementing", engine stubbed.
    # Act: call submit_implementation_signal with engine=mock.
    # Assert: task.status unchanged until reactor fires.

async def test_reactor_updates_status_cache_on_webhook(...):
    # Deliver synthetic item.transitioned webhook with to_state="impl_review".
    # Assert: task.status == "impl_review" after reactor handles.

async def test_engine_absent_path_writes_status_inline(...):
    # engine=None; signal adapter transition function writes status.
    # No reactor needed.
```

### Step 8: Integration regression
**File:** `tests/integration/test_feat006_e2e.py`
**Action:** Modify (if not already done by T-166)

With `await_reactor` already wrapping the assertions from T-166, this should already work. Validate.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/lifecycle/work_items.py` | Modify | Strip status writes; keep fallback. |
| `src/app/modules/ai/lifecycle/tasks.py` | Modify | Same. |
| `src/app/modules/ai/lifecycle/reactor.py` | Modify | Add `_update_status_cache`. |
| `src/app/modules/ai/lifecycle/service.py` | Modify (if any direct status writes). |
| `tests/modules/ai/lifecycle/test_reactor_status_cache.py` | Create | Unit tests. |

## Edge Cases & Risks
- **Stale-read window.** Documented in step 3. The `POST /tasks/{id}/implementation` returns 202 with the task's *prior* status if the caller reads the response body before the reactor runs. Acceptable — callers that care poll; the DTO shape is unchanged. Call this out in the ADR.
- **Engine-item-id lookup miss.** If a webhook arrives for an entity whose `engine_item_id` doesn't match any local row, the cache update silently skips. Could happen if the engine has items the orchestrator doesn't know about (e.g., a developer created an item directly in the engine — which should be impossible under the architecture, but defensive code is cheap). Log + skip.
- **Derivations are deferred.** T-169 doesn't move them. Confirm in T-172's integration test that derivations still work; if they don't, spin a follow-on.
- **Validation reads cache.** Transition functions call `_validate_transition(task.status, ...)` before issuing the engine call. That's still the cache — the cache might be stale from a concurrent transition. In practice, the engine rejects invalid transitions server-side, so the cache-read here is a belt-and-suspenders. Trust the engine's validation as authoritative; treat ours as advisory.

## Acceptance Verification
- [ ] `status` writes removed from all transition functions under engine-present.
- [ ] Engine-absent fallback preserved.
- [ ] Reactor updates status cache on every `item.transitioned` webhook.
- [ ] Unit tests cover both paths + webhook-to-cache flow.
- [ ] Derivation deferral documented (comment + follow-on issue or task mention).
- [ ] FEAT-006 e2e tests pass with `await_reactor`.
- [ ] `uv run pyright`, `ruff`, full suite green.
