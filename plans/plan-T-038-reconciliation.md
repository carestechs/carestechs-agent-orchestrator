# Implementation Plan: T-038 — Webhook-driven reconciliation (step update + loop wake)

## Task Reference
- **Task ID:** T-038
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-037, T-035

## Overview
Extend `ingest_engine_event` so a persisted webhook also updates the owning step's state-machine and wakes the run-loop coro. Split into three helpers (persist / reconcile / wake) so each is unit-testable.

## Steps

### 1. Create `src/app/modules/ai/reconciliation.py`
- Pure function `def next_step_state(current: StepStatus, event_type: WebhookEventType) -> tuple[StepStatus, bool]`: returns the new status + a `changed` bool. Monotonic transitions only:
  - `pending → (dispatched|in_progress|completed|failed)`
  - `dispatched → (in_progress|completed|failed)`
  - `in_progress → (completed|failed)`
  - terminal states (`completed`, `failed`) → no change.
- Mapping:
  - `NODE_STARTED` → `IN_PROGRESS`.
  - `NODE_FINISHED` → `COMPLETED`.
  - `NODE_FAILED` → `FAILED`.
  - `FLOW_TERMINATED` → no step-level transition (run-level concern; returns `(current, False)`).

### 2. Modify `src/app/modules/ai/service.py`
- Extract existing `ingest_engine_event` body into `_persist_event(...)` (returns the `WebhookEvent` row or the existing-dupe row, same contract).
- New helper `async _reconcile_step_from_event(event: WebhookEvent, db: AsyncSession, trace: TraceStore) -> bool`: loads `Step` by `engine_run_id`; computes `new_status, changed` via `reconciliation.next_step_state`; if changed, updates step fields (`status`, `node_result`/`error` from payload when terminal, `completed_at=now` when terminal); commits; writes trace line. Returns `changed`.
- New helper `async _notify_loop(event: WebhookEvent, supervisor: RunSupervisor) -> None`: if supervisor has `event.run_id`, calls `await supervisor.wake(event.run_id)`; else logs at DEBUG (normal for completed runs).
- Rewrite `ingest_engine_event` to call the three helpers in order: persist → (if new) reconcile → (if changed) notify.
- Dependencies: the function now takes `supervisor` and `trace_store` as additional args. Update the webhook route to inject them.

### 3. Modify `src/app/modules/ai/router.py`
- Extend `receive_engine_event` signature to inject `supervisor: Annotated[RunSupervisor, Depends(get_supervisor)]` and `trace: Annotated[TraceStore, Depends(get_trace_store)]`.
- Pass both through to `service.ingest_engine_event`.

### 4. Create `tests/modules/ai/test_reconciliation.py`
- Unit tests for `next_step_state`: every valid transition + every invalid rollback attempt (asserts `changed=False`).
- Integration: persist an event for a `pending` step → step ends `completed` with `node_result` populated + trace line appended + supervisor notified (assert via a `FakeSupervisor` that records calls).
- Out-of-order: send `NODE_STARTED` after `NODE_FINISHED` → no rollback, event still persisted, supervisor NOT notified (nothing changed).
- Late event for a terminal run: event persisted, step unchanged, supervisor NOT notified.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/reconciliation.py` | Create | `next_step_state` pure function. |
| `src/app/modules/ai/service.py` | Modify | Split `ingest_engine_event` into persist/reconcile/notify. |
| `src/app/modules/ai/router.py` | Modify | Inject supervisor + trace store deps. |
| `tests/modules/ai/test_reconciliation.py` | Create | Unit + integration tests. |
| `tests/modules/ai/test_routes_webhook.py` | Modify | Existing tests still pass; add supervisor fake to fixture. |

## Edge Cases & Risks
- **Concurrent webhook + cancel**: the reconciliation commit and the cancel commit can race. Cancel writes to `Run` table; reconcile writes to `Step` table — no direct conflict. But the loop should ignore reconciliation wake-ups when `cancel_requested` is set (checked in T-039's loop body).
- **Supervisor not present for run_id** (legitimate case: crash recovery, cold process): don't raise; log at DEBUG.
- Trace write happens AFTER the DB commit — if trace fails, DB state is still consistent; we log but don't re-raise.

## Acceptance Verification
- [ ] `NODE_FINISHED` on `dispatched` step → `completed` + `node_result` + `completed_at`.
- [ ] Out-of-order `NODE_STARTED` after `COMPLETED` → no change.
- [ ] Supervisor woken exactly once per state-changing event.
- [ ] Late event for terminal run: persisted, no mutation.
- [ ] Full webhook suite (T-026 + new) green.
