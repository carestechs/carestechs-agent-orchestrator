# Implementation Plan: T-119 — Defer endpoint (S14)

## Task Reference
- **Task ID:** T-119
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-113, T-114

## Overview
Admin-only `POST /api/v1/tasks/{id}/defer`. Writes `deferred_from`, transitions to `deferred`, fires `maybe_advance_to_ready`.

## Steps

### 1. Modify `src/app/modules/ai/schemas.py`
- `TaskDeferRequest(reason: str | None = None)`.

### 2. Modify `src/app/modules/ai/service.py`
- `async def defer_task_signal(session, task_id, req, *, actor) -> Task`:
  - `check_and_record` idempotency.
  - `async with session.begin():` wraps `lifecycle.tasks.defer_task(...)` → `lifecycle.work_items.maybe_advance_to_ready(task.work_item_id)`.

### 3. Modify `src/app/modules/ai/router.py`
- Route `/tasks/{id}/defer` depends on `require_actor_role(ActorRole.ADMIN)`.

### 4. Create `tests/modules/ai/test_router_tasks_defer.py`
- Happy path from each non-terminal state (7 params).
- Defer from `done` or `deferred` → `409`.
- Wrong role → `403`.
- Idempotent replay.
- When the deferred task is the last non-terminal in its work item, W5 fires and the work item advances to `ready`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/schemas.py` | Modify | `TaskDeferRequest`. |
| `src/app/modules/ai/service.py` | Modify | `defer_task_signal`. |
| `src/app/modules/ai/router.py` | Modify | One route. |
| `tests/modules/ai/test_router_tasks_defer.py` | Create | Route tests. |

## Edge Cases & Risks
- **Deferred_from preservation** — never cleared. If admin later re-approves a deferred task (not supported in v1), the field remains for audit.
- **Empty work item after defer** — all tasks deferred with no `done`; W5 advances to `ready` since `{done, deferred}` union covers all non-terminal.

## Acceptance Verification
- [ ] Endpoint live.
- [ ] Defer from every non-terminal state accepted.
- [ ] Defer from `done`/`deferred` → `409`.
- [ ] Admin-only; wrong role → `403`.
- [ ] W5 fires when appropriate.
- [ ] Idempotent replay.
- [ ] `uv run pyright`, `ruff`, route tests green.
