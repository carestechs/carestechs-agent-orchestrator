# Implementation Plan: T-130 — Engine webhook ingress + reactor

## Task Reference
- **Task ID:** T-130
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-128

## Overview
New webhook endpoint receives engine-emitted state-change events. Persists the event, then dispatches to a reactor that fires derivations (W2/W5/T4) and writes auxiliary rows (`Approval`, `TaskAssignment`, `TaskPlan`, `TaskImplementation`) using the correlation-id-keyed `PendingSignalContext` row stored by the signal endpoint.

## Steps

### 1. Modify `src/app/modules/ai/router.py`
- New route `POST /hooks/engine/lifecycle/item-transitioned`:
  - Depends on `require_engine_signature` (reused from FEAT-002's HMAC helper).
  - Parses body (see schema below), persists `WebhookEvent(source='engine', event_type='lifecycle_item_transitioned', engine_run_id=f"lifecycle:{itemId}", payload=raw, signature_ok=...)` via ON CONFLICT DO NOTHING on `dedupe_key=f"lifecycle:{itemId}:{transitionedAt}"`.
  - Calls `reactor.handle_transition(db, event)` on signature-OK deliveries.
  - Returns `202` with `{data: {received: true, eventId}}`.
  - 401 on bad signature after persisting.

### 2. Add request schema in `src/app/modules/ai/schemas.py`
- `class LifecycleItemTransitionedEvent(BaseModel):`
  - `item_id: uuid.UUID`, `workflow_name: str`, `from_status: str | None`, `to_status: str`, `correlation_id: uuid.UUID | None`, `transitioned_at: datetime`, `actor: str | None`.

### 3. Create `src/app/modules/ai/lifecycle/reactor.py`
- `async def handle_transition(db, event: LifecycleItemTransitionedEvent) -> None`:
  - Look up local `work_items` or `tasks` row by `engine_item_id=event.item_id` (which workflow determines which table).
  - Consume `PendingSignalContext` row by `correlation_id` (if present); capture `signal_name` + `payload`.
  - Dispatch by `(workflow_name, to_status)`:
    - `task_workflow` + `to_status=approved` → fire T4 automatically (orchestrator calls `engine_client.transition_item(item, to_status="assigning", correlation_id=…)`), also call W2 check on parent.
    - `task_workflow` + `to_status in {done, deferred}` → fire W5 check on parent.
    - Any → if `signal_name` indicates a signal with audit data (e.g., `reject-plan`, `assign-task`, `submit-plan`), write the appropriate aux row.
  - Delete the `PendingSignalContext` row on success.
  - Idempotent on replayed events via the `WebhookEvent.dedupe_key` UNIQUE.

### 4. Create `tests/modules/ai/lifecycle/test_reactor.py`
- Synthetic `LifecycleItemTransitionedEvent` + `PendingSignalContext` seeded in DB.
- Test cases:
  - Task `proposed → approved` fires T4 (reactor calls engine to transition `approved → assigning`) + W2 (if first approved task in work item).
  - Task `impl_review → done` fires W5 (calls engine for parent work item `in_progress → ready` when all siblings terminal).
  - Task `plan_review → planning` (rejection) writes `Approval(stage=plan, decision=reject, feedback=...)` from `PendingSignalContext`.
  - Idempotent replay: same event twice → no duplicate side effects.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/router.py` | Modify | New route. |
| `src/app/modules/ai/schemas.py` | Modify | Event DTO. |
| `src/app/modules/ai/lifecycle/reactor.py` | Create | Dispatcher. |
| `tests/modules/ai/lifecycle/test_reactor.py` | Create | Reactor tests. |

## Edge Cases & Risks
- **T4 cascade recursion.** Reactor fires T4 by calling the engine; the engine then sends another webhook back for `approved → assigning`. Reactor sees `to_status=assigning`, no work to do. Fine, but make sure the reactor doesn't loop — the `task_workflow`/`assigning` case must be a no-op.
- **Missing `PendingSignalContext`.** If a transition arrives without a correlation row (e.g., operator hit the engine directly, or the context was already consumed), log a warning and still do derivations. Don't hard-fail.
- **Concurrent webhooks.** Engine may deliver out of order under retry. Derivations should be idempotent and commutative where possible. W5 already is; W2 already is.

## Acceptance Verification
- [ ] Endpoint live with HMAC verification.
- [ ] Reactor dispatches correctly for W2, W5, T4.
- [ ] Aux rows written from `PendingSignalContext` payload.
- [ ] Idempotent replay via `WebhookEvent.dedupe_key`.
- [ ] `uv run pyright`, `ruff`, tests green.
