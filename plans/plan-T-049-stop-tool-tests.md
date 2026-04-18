# Implementation Plan: T-049 — Stop-condition + tool-builder unit tests

## Task Reference
- **Task ID:** T-049
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-033, T-034

## Overview
Table-driven parameterized tests covering priority ordering + gating logic beyond the basic cases from T-033 / T-034.

## Steps

### 1. Extend `tests/modules/ai/test_stop_conditions.py`

Add a parameterized priority-conflict matrix:
```python
@pytest.mark.parametrize(
    ("state_kwargs", "expected"),
    [
        # (state flag combos → expected stop reason)
        ({"cancel_requested": True, "last_engine_error": Exception()}, StopReason.CANCELLED),
        ({"cancel_requested": True, "max_steps": 1, "step_count": 1}, StopReason.CANCELLED),
        ({"last_engine_error": Exception(), "max_steps": 1, "step_count": 1}, StopReason.ERROR),
        ({"max_steps": 1, "step_count": 1, "last_tool": _terminate_tool()}, StopReason.BUDGET_EXCEEDED),
        ({"last_tool": _terminate_tool(), "last_tool_is_terminal_node": True}, StopReason.POLICY_TERMINATED),
        ({}, None),
    ],
    ids=[...],
)
```

Boundary tests:
- `step_count == max_steps - 1` → no stop.
- `step_count == max_steps` → `BUDGET_EXCEEDED`.
- `token_count` vs `max_tokens` same boundary.

### 2. Extend `tests/modules/ai/test_tools_builder.py`

Add:
- Tool order: `build_tools(agent, all_nodes)` returns tools in agent's declared node order, with `terminate` last — assert exact positions.
- Empty `nodes` on agent → returns `[terminate]` only.
- `available_nodes` containing nodes not in the agent → no error, those entries ignored.
- Two calls return independent lists (not a shared mutable object).
- `TERMINATE_TOOL_NAME` is exported and stable.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/modules/ai/test_stop_conditions.py` | Modify | Priority matrix + boundary tests. |
| `tests/modules/ai/test_tools_builder.py` | Modify | Order + independence tests. |

## Edge Cases & Risks
- Keeping the priority matrix readable: use descriptive `ids=` in `pytest.parametrize` so CI output names each case.
- Use small helper factories (`_terminate_tool()`, `_make_state(**kwargs)`) to avoid boilerplate.

## Acceptance Verification
- [ ] All priority-conflict cases documented and passing.
- [ ] Boundary tests cover off-by-one on both step + token budgets.
- [ ] Tool-builder order assertions explicit (indices, not just `in`).
- [ ] `uv run pytest` green.
