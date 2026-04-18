# Implementation Plan: T-057 — Integration: webhook-driven advancement timing (AC-7)

## Task Reference
- **Task ID:** T-057
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-038, T-054

## Overview
Measure wall-clock delta between a webhook response returning 202 and the next `PolicyCall.created_at`. Assert ≤100 ms local / ≤500 ms CI.

## Steps

### 1. Create `tests/integration/test_webhook_timing.py`

```python
async def test_webhook_to_next_policy_call_under_100ms(...):
    # 1. Warm the DB with a throwaway run (so first-connection overhead doesn't skew).
    # 2. Stub policy scripted for 2 steps.
    # 3. EngineEcho with delay=0 (instant webhook).
    # 4. POST /api/v1/runs; capture first dispatch's engine_run_id.
    # 5. Subscribe to PolicyCall rows (poll every 5 ms until the 2nd PolicyCall appears).
    # 6. Alternative instrumentation: patch time.perf_counter into the webhook route after
    #    the 202 response is sent, and into run_loop.service BEFORE the 2nd policy call.
    # 7. Assertions:
    #    - (t_policy_2 - t_webhook_response) < 0.5 seconds (CI-friendly)
    #    - Local dev target documented: < 100 ms
```

### 2. Make timing instrumentation pluggable

Rather than patch production code, use a small test-only `TimingRecorder` helper:
- `record(event_name)` stores `(name, time.perf_counter())` in a thread-safe list.
- Add opt-in hooks in `service.ingest_engine_event` (after 202 response prepared) and `runtime.run_loop` (before each policy call) that call `TimingRecorder.record` IF a recorder is attached via `contextvars`. No-op when absent.
- Test attaches a recorder via a contextvar; assertions read from it.

**Alternative** (simpler): subscribe to DB rows — poll `PolicyCall` table, measure wall-clock. Less precise but no production code churn.

Pick the DB-polling approach for v1 unless timing assertions are consistently flaky.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_webhook_timing.py` | Create | Timing assertion test. |

## Edge Cases & Risks
- Cold DB + cold asyncpg pool can cost 50-100 ms on first connection — the warmup run is essential.
- CI overhead (shared Docker Postgres) makes tight bounds impossible. Document the local-vs-CI split clearly.
- If the test flakes > 1 %, consider marking it `@pytest.mark.timing` and gating behind an env flag.

## Acceptance Verification
- [ ] Timing assertion passes on a warm DB.
- [ ] CI bound (≤500 ms) documented in test docstring.
- [ ] Test self-documents: on failure, prints the measured delta.
