# Implementation Plan: T-056 — Integration: engine failure → error stop

## Task Reference
- **Task ID:** T-056
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-039, T-036

## Overview
Engine returns 502 on first dispatch → run terminates `error`. Step row marked `failed` with populated `error` JSONB. Trace captures the failure. No raw `httpx` leaks.

## Steps

### 1. Create `tests/integration/test_engine_failure.py`

```python
async def test_engine_502_ends_run_as_error(...):
    # 1. Configure EngineEcho with fail_on_step_number=1 (502 + correlation-id header).
    # 2. Stub policy scripted for 1 step.
    # 3. POST /api/v1/runs; wait for terminal.
    # 4. Assertions:
    #    - Run: status=failed, stop_reason=error, final_state.error_type == "engine-error"
    #    - Step 1: status=failed, error.engine_http_status == 502,
    #              error.engine_correlation_id populated (if mock sent it),
    #              error.original_body present.
    #    - No uncaught httpx exception in captured logs.
    #    - JSONL has a final step line with status=failed and a final run-termination line.
```

### 2. Variant: connection error

Same test file, another function:
- EngineEcho injects `httpx.ConnectError` on dispatch.
- Same assertions, but `engine_http_status is None` on the error JSONB.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_engine_failure.py` | Create | 502 and connection-error scenarios. |

## Edge Cases & Risks
- The EngineEcho helper from T-054 must support `fail_on_step_number` — update the helper in T-054 OR add it here (prefer T-054 to centralize the helper).
- Captured logs: use `caplog.at_level(logging.WARNING)` to capture; assert no `ERROR`-level logs unless they're our controlled "engine dispatch failed" message.

## Acceptance Verification
- [ ] 502 → run ends `error` with populated error metadata.
- [ ] Connection error → same, `engine_http_status=None`.
- [ ] No raw httpx exception leaks into logs.
- [ ] Trace records the failure.
