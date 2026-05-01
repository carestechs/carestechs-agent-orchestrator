# Bug Report: BUG-007 — propose_tasks inserts local row after firing T2/T4

> **Purpose**: Capture the ordering bug surfaced by the live `lifecycle-agent@0.3.0` run after BUG-005/006 fixed connectivity + signature verification. Filed and resolved in the same PR (operator diagnosis).

---

## 1. Identity

| Field | Value |
|-------|-------|
| **ID** | BUG-007 |
| **Summary** | `ProposeTasksExecutor` inserts the local `tasks` row **after** firing the T2 (`proposed → approved`) and T4 (`approved → assigning`) transitions on the engine. The engine fires `item.transitioned` webhooks for those transitions before the local row exists; the lifecycle reactor looks up the task by `engine_item_id`, finds nothing, and skips status-cache writes / effector dispatch / W2/W5 derivations. Result: assign_task's downstream T5 never resolves cleanly because the engine-task→local-task linkage was never established. |
| **Severity** | High (blocks every v0.3.0 end-to-end run after engine connectivity is in place; v0.1.0 unaffected) |
| **Status** | Resolved |
| **Reported By** | Live `lifecycle-agent@0.3.0` run (operator diagnosis) |
| **Date Reported** | 2026-05-01 |
| **Date First Observed** | 2026-05-01 (after BUG-005 + BUG-006 unblocked webhook delivery + signature verification) |
| **Related** | BUG-004 (introduced `ProposeTasksExecutor`), BUG-005, BUG-006 |

---

## 2. Steps to Reproduce

**Preconditions:** orchestrator + flow-engine running; `PUBLIC_BASE_URL` set to the orchestrator's container DNS name; webhook subscriptions registered; HMAC signature verification working.

1. Start `lifecycle-agent@0.3.0`.
2. Run reaches `propose_tasks`. Per task:
   - `create_item(task_workflow)` → engine task id (e.g. `caef38bc-…`).
   - `transition_item(approved)` — engine fires webhook.
   - `transition_item(assigning)` — engine fires webhook.
   - `_upsert_local_task(...)` — local row inserted.
3. Webhooks for the two transitions arrive at `/hooks/engine/lifecycle/item-transitioned`.
4. **Observe** in orchestrator logs:
   ```
   status cache miss for task engine_item_id=caef38bc-…
   effector dispatch: task engine_item_id=caef38bc-… not found locally; skipping
   task lifecycle webhook for unknown engine_item_id caef38bc-…; skipping
   ```
5. The webhooks arrived *before* `_upsert_local_task` ran — the reactor's lookup failed, every downstream derivation skipped.

**Reproducibility:** Always — deterministic given the executor's call ordering.

---

## 3. Root Cause

`src/app/modules/ai/executors/propose_tasks.py` runs the per-task sequence as:

```python
for task in memory.tasks:
    engine_task_id = await client.create_item(...)
    await client.transition_item(approved)   # webhook fires
    await client.transition_item(assigning)  # webhook fires
    await self._upsert_local_task(...)       # local row finally inserted
```

The engine processes each `transition_item` synchronously *and* fires its webhook before `transition_item` returns (or close enough that the orchestrator doesn't beat the webhook to its DB write). When the webhook arrives, the lifecycle reactor's three lookup-by-`engine_item_id` paths (`_update_status_cache`, `_dispatch_effectors`, `_handle_task_transition`) all miss and skip. The orchestrator's view of the engine task and its local mirror are decoupled.

`_wake_dispatch` (FEAT-010) is keyed on `correlation_id` from `Dispatch.intake`, not on `engine_item_id`, so subsequent engine-mode dispatches (assign_task, generate_plan, etc.) can still wake — but the FEAT-008 derivations (effector dispatch, status cache, W2/W5) silently no-op.

---

## 4. Fix

Reorder the per-task loop so the local row is inserted **and committed** before the transitions fire, and merge the engine_item_id into memory per-task (not after the loop):

```python
for task in memory.tasks:
    engine_task_id = await client.create_item(...)
    await self._upsert_local_task(...)                    # row visible from this point
    await self._merge_engine_ids_into_memory({task.id: engine_task_id})
    await client.transition_item(approved)                 # webhook finds the row
    await client.transition_item(assigning)                # webhook finds the row
```

Per-task memory merge means a later task's failure preserves earlier tasks' engine ids in `LifecycleMemory.tasks[i].engineItemId`.

---

## 5. Verification

- New `tests/modules/ai/executors/test_propose_tasks.py` (2 cases):
  - `test_t2_finds_local_row_already_committed` — uses an `on_transition_item` hook on a recording client; when the T2 transition is about to fire, queries a *fresh* DB session for the `tasks` row keyed on the engine id and asserts it is already committed. Plus asserts the precise call sequence: `create_item`, `transition_item(approved)`, `transition_item(assigning)`, then the work-item W2.
  - `test_memory_carries_engine_id_per_task_after_dispatch` — multi-task scenario; both tasks' `engineItemId` are persisted in `LifecycleMemory.tasks[*]` after dispatch.
- Existing v0.3.0 e2e + rejection tests still pass (the respx mocks return 200 without firing real webhooks, so the previous order didn't bite at unit-test scope).
- Live re-run will exercise the real engine and produce status-cache hits (no more "status cache miss" log lines for the propose_tasks transitions).

---

## 6. Out of Scope

- The wake-on-correlation pipeline already tolerates a missing local row (it keys on `correlation_id` only); no changes there.
- Effector / status-cache / derivation paths still skip when no local row exists — that's the right behaviour for engine-initiated transitions outside the orchestrator's flow. We're fixing only the case where the orchestrator itself created the engine entity.

---

## Changelog

- 2026-05-01 — Filed and resolved in the same PR; operator diagnosis.
