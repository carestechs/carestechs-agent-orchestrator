# Implementation Plan: T-116 — Task proposal + assignment endpoints (S5-S7)

## Task Reference
- **Task ID:** T-116
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-113, T-114

## Overview
Three admin-only endpoints: `POST /api/v1/tasks/{id}/approve`, `/reject`, `/assign`. `/approve` fires T4 + `maybe_advance_to_in_progress` in the same transaction.

## Steps

### 1. Modify `src/app/modules/ai/schemas.py`
- DTOs:
  - `TaskRejectRequest(feedback: str)` — `feedback: str = Field(min_length=1)`.
  - `TaskAssignRequest(assigneeType: AssigneeType, assigneeId: str)`.
  - Approve body is `{}`; reuse an empty `TaskApproveRequest(BaseModel)`.

### 2. Modify `src/app/modules/ai/service.py`
- Adapters over T-113:
  - `async def approve_task_signal(session, task_id, *, actor) -> Task`:
    - `check_and_record` idempotency.
    - `async with session.begin():` wraps: `lifecycle.tasks.approve_task(...)` → `lifecycle.work_items.maybe_advance_to_in_progress(work_item_id)`.
  - `async def reject_task_signal(session, task_id, req, *, actor) -> Task` — delegates.
  - `async def assign_task_signal(session, task_id, req, *, actor) -> TaskAssignment`.

### 3. Modify `src/app/modules/ai/router.py`
- 3 routes under `/api/v1/tasks/{id}/...`:
  - `/approve` (admin), `/reject` (admin), `/assign` (admin).
- Each returns `LifecycleSignalResponse`; wrong role → `403`; illegal transition → `409`; missing feedback → `422` (raised by Pydantic `min_length=1`).

### 4. Create `tests/modules/ai/test_router_tasks_proposal.py`
- Happy-path per endpoint.
- Wrong role: call `/approve` as `dev` → `403`.
- Illegal state: `/assign` when `status=planning` → `409`.
- Reject without feedback → `422`.
- Idempotent replay on each → `alreadyReceived=true`, no duplicate `Approval` row, no double T4.
- First approve fires W2; second approve on another task in same work item does NOT re-fire (derivation idempotent).
- Assign on an already-active assignment supersedes cleanly.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/schemas.py` | Modify | Three request DTOs. |
| `src/app/modules/ai/service.py` | Modify | Three service adapters. |
| `src/app/modules/ai/router.py` | Modify | Three new routes. |
| `tests/modules/ai/test_router_tasks_proposal.py` | Create | Route tests. |

## Edge Cases & Risks
- **T4 + W2 atomicity** — all three writes (approve, T4 transition, W2 derivation) must live in one transaction. If W2 fails, roll everything back and surface the error.
- **Assignment supersede race** — the partial-unique index from T-109 ensures at most one active; wrap supersede + insert in `SELECT ... FOR UPDATE` on the task row to serialize.
- **W2 no-op on non-first approval** — verified in T-112's test; rely on it here.

## Acceptance Verification
- [ ] 3 endpoints live under `/api/v1/tasks/{id}/...`.
- [ ] Approve fires T4 + W2 atomically.
- [ ] Reject requires non-empty feedback (`422` else).
- [ ] Assign supersedes prior active assignment.
- [ ] Wrong role → `403`.
- [ ] Idempotent replay.
- [ ] `uv run pyright`, `ruff`, route tests green.
