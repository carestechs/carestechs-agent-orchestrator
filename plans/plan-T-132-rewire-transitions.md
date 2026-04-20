# Implementation Plan: T-132 — Rewire transitions to the engine

## Task Reference
- **Task ID:** T-132
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** L
- **Dependencies:** T-128, T-131

## Overview
Every transition function in `lifecycle/work_items.py` and `lifecycle/tasks.py` calls the engine's `POST /api/items/{id}/transitions` instead of performing a local UPDATE. The engine validates legality and records history; orchestrator's service functions shrink to thin adapters. Derivations + `Approval` writes move out (now handled by T-130's reactor).

## Steps

### 1. Refactor `src/app/modules/ai/lifecycle/work_items.py`
- Drop `_load_locked`, `_forbidden`, `_TERMINAL_TASK_STATUSES`.
- New shape for transitions — example:
  ```python
  async def lock_work_item(db, work_item_id, *, actor, engine, correlation_id):
      wi = await db.scalar(select(WorkItem).where(WorkItem.id == work_item_id))
      if wi is None:
          raise NotFoundError(...)
      try:
          await engine.transition_item(
              item_id=wi.engine_item_id,
              to_status="locked",
              correlation_id=correlation_id,
              actor=actor,
          )
      except EngineError as e:
          if e.http_status == 422:
              raise ConflictError(e.detail)
          raise
      return wi
  ```
- `maybe_advance_to_in_progress` / `maybe_advance_to_ready` now call the engine but gate on "all tasks in workflow-terminal states" — query the orchestrator's tasks table for work_item children, then for each query the engine (or cached state) to check terminal. Derivations run from the reactor, not from here.
- `open_work_item` creates the engine item + inserts the local `WorkItem` row with that `engine_item_id`. Two-phase: engine first (so we have the id), then local insert. If local insert fails, leave the engine item orphaned (log warning; retention job covers it).

### 2. Refactor `src/app/modules/ai/lifecycle/tasks.py`
- Same pattern: every transition becomes an engine call.
- `propose_task` creates the engine item first.
- `approve_task` no longer does T4 inline — the engine webhook reactor does. Just transitions `proposed → approved`.
- `reject_task_proposal` / `reject_plan` / `reject_review`: no engine transition (status doesn't change). The `Approval` row write happens via the signal service adapter → `PendingSignalContext` → reactor? No, for rejections the reactor isn't triggered (no engine transition). So rejection rows get written **by the signal adapter directly**, not via the reactor. Document this asymmetry explicitly in a module docstring.
- `assign_task`: engine transition `assigning → planning`; the `TaskAssignment` row is auxiliary data written from the adapter via `PendingSignalContext`.

### 3. Update imports + types
- Add `FlowEngineLifecycleClient` as a dep in every transition signature.
- Remove the now-unused `TaskAssignment`, `Approval` writes that used to happen inline.

### 4. Update `tests/modules/ai/lifecycle/test_work_items.py` + `test_tasks.py`
- Replace direct-state assertions with calls to a mocked engine client.
- Use `respx` at the HTTP boundary or a fake `FlowEngineLifecycleClient` that records calls.
- Tests become: "calling `lock_work_item` calls `engine.transition_item(item_id=..., to_status='locked')`".

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/lifecycle/work_items.py` | Modify | Major refactor. |
| `src/app/modules/ai/lifecycle/tasks.py` | Modify | Major refactor. |
| `tests/modules/ai/lifecycle/test_work_items.py` | Modify | Assert on engine calls. |
| `tests/modules/ai/lifecycle/test_tasks.py` | Modify | Same. |

## Edge Cases & Risks
- **Two-phase create ordering.** Engine item created first, local row second. If the second step fails, the engine has an orphan. Log; rely on eventual cleanup job (out of scope).
- **Concurrent transitions.** Two signals hit the engine simultaneously for the same item. Engine serializes; one wins, the other gets 422 ("already transitioned"). Orchestrator surfaces as `ConflictError`.
- **Rejection asymmetry.** Rejections don't call the engine. Write them synchronously from the signal adapter (not via reactor). Document clearly.

## Acceptance Verification
- [ ] All 8 work-item + 12 task transitions route through the engine client.
- [ ] 422 from engine → `ConflictError`; 404 → `NotFoundError`.
- [ ] No local UPDATE of state columns (they no longer exist).
- [ ] Rejection paths still write `Approval` rows.
- [ ] Updated tests green.
- [ ] `uv run pyright`, `ruff`.
