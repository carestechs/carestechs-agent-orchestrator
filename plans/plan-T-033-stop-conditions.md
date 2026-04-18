# Implementation Plan: T-033 — Stop-condition module (pure functions)

## Task Reference
- **Task ID:** T-033
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-031

## Overview
Five pure `(RuntimeState) -> StopReason | None` rules + an `evaluate` that applies them in documented priority order. No I/O, no DB — side-effect-free so the runtime loop stays simple.

## Steps

### 1. Create `src/app/modules/ai/stop_conditions.py`
- Import `StopReason` from `enums.py`, `ToolCall` from `core.llm`, `dataclasses`.
- Define `@dataclass(frozen=True, slots=True) class RuntimeState`: `last_tool: ToolCall | None`, `step_count: int`, `token_count: int`, `max_steps: int | None`, `max_tokens: int | None`, `last_policy_error: Exception | None`, `last_engine_error: Exception | None`, `cancel_requested: bool`, `terminal_nodes: frozenset[str]`.
- Function `is_cancelled(s) -> StopReason | None`: return `StopReason.CANCELLED` iff `s.cancel_requested`.
- Function `is_error(s) -> StopReason | None`: return `StopReason.ERROR` iff `s.last_policy_error is not None or s.last_engine_error is not None`.
- Function `is_budget_exceeded(s) -> StopReason | None`: return `StopReason.BUDGET_EXCEEDED` iff `(max_steps is not None and step_count >= max_steps) or (max_tokens is not None and token_count >= max_tokens)`.
- Function `is_policy_terminated(s) -> StopReason | None`: return `StopReason.POLICY_TERMINATED` iff `s.last_tool is not None and s.last_tool.name == "terminate"`.
- Function `is_done_node(s) -> StopReason | None`: return `StopReason.DONE_NODE` iff `s.last_tool is not None and s.last_tool.name in s.terminal_nodes`.
- `_PRIORITY: list[Callable[[RuntimeState], StopReason | None]] = [is_cancelled, is_error, is_budget_exceeded, is_policy_terminated, is_done_node]`.
- `def evaluate(state: RuntimeState) -> StopReason | None`: iterate `_PRIORITY`; return first non-None.

### 2. Create `tests/modules/ai/test_stop_conditions.py`
- Parameterized tests: one per rule + edge cases.
- Priority conflict test: cancel + budget both set → `CANCELLED`.
- All-None case → `evaluate` returns `None`.
- Hitting exactly `max_steps` → `BUDGET_EXCEEDED` (off-by-one guard).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/stop_conditions.py` | Create | 5 pure rules + `evaluate`. |
| `tests/modules/ai/test_stop_conditions.py` | Create | Parameterized rule + priority tests. |

## Edge Cases & Risks
- `terminal_nodes` must be a `frozenset` so `RuntimeState` stays hashable and immutable — unit test asserts `RuntimeState` is hashable.
- Priority order is load-bearing: document the rationale inline ("cancel wins over error so user intent isn't masked by a concurrent failure").

## Acceptance Verification
- [ ] Every rule has happy + negative tests.
- [ ] Priority-order tests pass.
- [ ] `evaluate` branch coverage = 100 % on this module (verify via `pytest --cov=src/app/modules/ai/stop_conditions`).
