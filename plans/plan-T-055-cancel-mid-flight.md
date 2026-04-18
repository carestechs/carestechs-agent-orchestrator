# Implementation Plan: T-055 — Integration: cancel mid-flight

## Task Reference
- **Task ID:** T-055
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-042, T-054

## Overview
Start a multi-step run with a deliberately slow engine mock. Cancel mid-flight. Assert terminal transition within 500 ms and that a late webhook after cancel is handled gracefully.

## Steps

### 1. Create `tests/integration/test_run_cancel.py`

Scenario:
- Stub policy scripted for ≥ 3 steps.
- `EngineEcho` configured with `delay_seconds=2.0` per dispatch — the first webhook is still pending when we cancel.
- Start run via POST `/api/v1/runs`.
- Poll `GET /runs/{id}` until `status == running`.
- Capture timestamp `t_cancel = time.perf_counter()`.
- POST `/runs/{id}/cancel` with a reason.
- Poll until terminal; capture `t_terminal`.
- Assertions:
  - `t_terminal - t_cancel < 0.5` (in local dev; CI bound 2 s documented in a `pytest.mark.timeout`).
  - Run row: `status == cancelled`, `stop_reason == cancelled`, `final_state.cancel_reason == "<reason>"`.
  - Step row for the in-flight step exists but is NOT terminal yet (or is `failed` if cancel injection raced the dispatch).
  - JSONL trace has a final line documenting cancellation.

### 2. Late-webhook-after-cancel sub-test

Same test file, separate test function:
- Run the cancel scenario through to terminal.
- Manually POST `/hooks/engine/events` with a signed payload that WOULD correspond to the in-flight step's webhook (use the step's `engine_run_id` + a `NODE_FINISHED` event).
- Assert HTTP response is 202 (webhook accepted).
- Assert the step row was NOT updated (remains in whatever pre-cancel state it was).
- Assert no new `Run` or `Step` rows leaked.
- Assert one extra `WebhookEvent` row persisted.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_run_cancel.py` | Create | Mid-flight cancel + late-webhook. |

## Edge Cases & Risks
- Timing assertions will flake on cold CI; use 2 s upper bound in CI, document the 500 ms local target in docstring.
- `asyncio.wait_for` timeout inside the test (via `pytest-asyncio` timeout) gives a graceful failure if the cancel doesn't work at all.
- Race: cancel may complete before the loop even reaches its first `await_wake`. Either outcome is valid as long as the run ends `cancelled`.

## Acceptance Verification
- [ ] Cancel terminates run within bound.
- [ ] Late webhook returns 202, persists, but does not mutate step.
- [ ] No leaked rows at teardown (savepoint rollback cleans up).
- [ ] Test passes on local + CI reliably.
