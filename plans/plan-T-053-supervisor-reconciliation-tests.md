# Implementation Plan: T-053 — Supervisor + reconciliation unit tests

## Task Reference
- **Task ID:** T-053
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-037, T-038

## Overview
Deeper coverage of supervisor invariants (wake-up-loss, cancel-while-awaiting, shutdown ordering) and reconciliation state-machine (all 16 transition combos + all 4 event types).

## Steps

### 1. Extend `tests/modules/ai/test_supervisor.py`

Add:
- `test_wake_before_wait_is_preserved`: `spawn(...)` a coro that sleeps 50 ms before calling `await_wake`; `wake` called immediately after spawn; coro observes the wake. (Asserts `Event.wait()` semantics hold.)
- `test_cancel_while_awaiting_wake`: spawn a coro that awaits `await_wake` forever; `cancel()` interrupts it within 50 ms.
- `test_shutdown_cancels_remaining_tasks`: spawn 3 long-running coros; call `shutdown(grace=0.1)`; assert all 3 exited via `CancelledError`.
- `test_exception_in_coro_does_not_crash_supervisor`: spawn a coro that raises; spawn another afterwards and assert it runs.
- `test_concurrent_spawns_preserved`: 100 `spawn` calls in parallel; each coro increments a counter; final count == 100.

### 2. Extend `tests/modules/ai/test_reconciliation.py`

Add:
- `test_next_step_state_all_transitions`:
  - Parameterized over (current_status, event_type) for all combinations.
  - Assert expected new status + `changed` flag.
- `test_late_event_for_terminal_run`:
  - Set `Run.status=cancelled`; call reconciliation with a `NODE_FINISHED` event.
  - Assert step was NOT updated (even if step was still pending).
  - Event was persisted.
  - Supervisor was NOT woken.
- `test_flow_terminated_event_writes_final_state`:
  - `FLOW_TERMINATED` event with payload `{"reason": "engine-side-terminated"}`.
  - Assert `Run.final_state` updated with that payload.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/modules/ai/test_supervisor.py` | Modify | 5 new tests. |
| `tests/modules/ai/test_reconciliation.py` | Modify | 3 new transition / terminal / FLOW_TERMINATED tests. |

## Edge Cases & Risks
- `test_shutdown_cancels_remaining_tasks` timing: use `grace=0.1` so tests finish fast; supervisor should force-cancel after grace expires.
- Wake-before-wait test must demonstrate that `asyncio.Event` preserves state — otherwise there's a race.

## Acceptance Verification
- [ ] All 16 transition combos parameterized.
- [ ] Wake-before-wait test passes (invariant proven).
- [ ] Cancel-while-awaiting test holds timing bound.
- [ ] 100-spawn concurrency test passes.
- [ ] Late-event and FLOW_TERMINATED scenarios documented.
