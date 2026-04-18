# Implementation Plan: T-042 — `cancel_run` state transition + supervisor cancel

## Task Reference
- **Task ID:** T-042
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-040

## Overview
Flip terminal state on the DB first, then cancel the supervised task. DB-first ordering prevents a concurrent webhook from reviving a cancelled run.

## Steps

### 1. Modify `src/app/modules/ai/service.py`
Replace `cancel_run(run_id, request, db, supervisor)` body:
1. `run = await repository.get_run_by_id(db, run_id)` → `NotFoundError` if None.
2. If `run.status` is already terminal (`completed|failed|cancelled`): return `RunSummaryDto.model_validate(run, from_attributes=True)` — idempotent no-op.
3. Else update fields: `status=RunStatus.CANCELLED`, `stop_reason=StopReason.CANCELLED`, `ended_at=now_utc()`, `final_state = {"cancel_reason": request.reason, "cancelled_via": "api"}` (merged with existing `final_state` if present).
4. `await db.commit()`.
5. `await supervisor.cancel(run_id)` — awaits the task's exit (bounded by supervisor's grace).
6. Refresh + return `RunSummaryDto`.

### 2. Modify `src/app/modules/ai/router.py`
- Extend `cancel_run` route to inject `supervisor: RunSupervisor = Depends(get_supervisor)` and forward.

### 3. Create `tests/modules/ai/test_service_cancel.py`
- Cancel a `pending` run → terminal within 500 ms (assert timing + DTO fields).
- Cancel a run that completed — second call returns the same summary, no supervisor call.
- Cancel an unknown id → `NotFoundError`.
- Late webhook after cancel: use the reconciliation helper directly, assert step transition is skipped when parent `Run.status == cancelled` (cross-reference with T-038).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/service.py` | Modify | Real `cancel_run`. |
| `src/app/modules/ai/router.py` | Modify | Inject supervisor. |
| `tests/modules/ai/test_service_cancel.py` | Create | Happy + idempotence + late-webhook. |

## Edge Cases & Risks
- Race: webhook arrives between DB commit and `supervisor.cancel()`. The loop's next `evaluate` will observe `cancel_requested` and terminate anyway — no data corruption.
- Process crash mid-cancel leaves the DB in `cancelled` state; next startup's zombie reconciliation (T-045) does nothing because row is already terminal. Safe.

## Acceptance Verification
- [ ] Cancel < 500 ms turnaround.
- [ ] Idempotent second cancel.
- [ ] Unknown id → 404.
- [ ] Late webhook after cancel does not mutate step.
- [ ] Integration test T-055 passes.
