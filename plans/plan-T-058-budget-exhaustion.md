# Implementation Plan: T-058 — Integration: budget exhaustion stop

## Task Reference
- **Task ID:** T-058
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-033, T-039

## Overview
Start a run with `maxSteps=2` but a 4-step policy script. Assert run ends `budget_exceeded` after exactly 2 steps.

## Steps

### 1. Create `tests/integration/test_run_budget.py`

```python
async def test_max_steps_budget_stops_run(...):
    # 1. Stub policy script: 4 non-terminate tool calls.
    # 2. EngineEcho with delay=0.
    # 3. POST /api/v1/runs with intake + budget={"maxSteps": 2}.
    # 4. Wait for terminal.
    # 5. Assertions:
    #    - Run: status=failed? or completed? → status=FAILED, stop_reason=BUDGET_EXCEEDED
    #      (Design decision: budget exhaustion is a FAILED status, not COMPLETED,
    #       since the agent didn't reach its terminal node.)
    #    - exactly 2 Step rows exist.
    #    - exactly 2 PolicyCall rows exist.
    #    - final_state contains budget snapshot: {"step_count": 2, "max_steps": 2}

async def test_max_tokens_budget_stops_run(...):
    # Similar but uses tokens instead of steps. Stub policy's ToolCall.usage.input_tokens
    # is configurable; set each call to 500 tokens, max_tokens=1000 → stops after 2 calls.
```

### 2. Clarify status mapping in T-039

The runtime loop maps `StopReason` → `RunStatus`:
- `DONE_NODE` → `COMPLETED`.
- `POLICY_TERMINATED` → `COMPLETED`.
- `BUDGET_EXCEEDED` → `FAILED`.
- `ERROR` → `FAILED`.
- `CANCELLED` → `CANCELLED`.

Document the mapping in `runtime.py` docstring. The test verifies the mapping for budget.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_run_budget.py` | Create | Both budget types tested. |
| `src/app/modules/ai/runtime.py` | Modify (doc only) | Document StopReason→RunStatus mapping. |

## Edge Cases & Risks
- Off-by-one: `max_steps=2` means the loop stops AT `step_count == 2`, after the 2nd step completes. The 3rd policy call never happens.
- `default_budget` in YAML may override request-level budget — document precedence (request wins if both set).

## Acceptance Verification
- [ ] Both budget types stop the run correctly.
- [ ] Exactly `max_steps` step rows exist.
- [ ] Run status mapping per documented table.
- [ ] `final_state` contains budget snapshot for debugging.
