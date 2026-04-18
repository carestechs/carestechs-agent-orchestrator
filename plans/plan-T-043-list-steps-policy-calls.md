# Implementation Plan: T-043 — `list_steps` + `list_policy_calls` with pagination

## Task Reference
- **Task ID:** T-043
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-039

## Overview
Symmetrical paginated reads for steps and policy calls. Shared helper in `repository.py`.

## Steps

### 1. Modify `src/app/modules/ai/repository.py`
- `async def count_steps_filtered(db, run_id) -> int`.
- `async def select_steps(db, run_id, *, page, page_size) -> list[Step]`: ORDER BY `step_number ASC`.
- `async def count_policy_calls(db, run_id) -> int`.
- `async def select_policy_calls(db, run_id, *, page, page_size) -> list[PolicyCall]`: ORDER BY `created_at ASC, id ASC`.

### 2. Modify `src/app/modules/ai/service.py`
Replace both stubs:
- `list_steps`: validate run exists (`repository.get_run_by_id` → `NotFoundError`); return `(DTOs, total)`.
- `list_policy_calls`: same pattern. Policies are append-only so ascending is the natural order.

### 3. Create `tests/modules/ai/test_service_lists.py`
- Seed a run with 5 steps + 5 policy calls.
- Pagination: `page_size=2`, 3 pages, third page has 1 item.
- Unknown run id → `NotFoundError` (not empty list).
- Order asserts: steps ascending by `step_number`; policy calls ascending by `created_at`.
- DTO field presence assertions (`node_inputs`, `tool_arguments` non-null).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/repository.py` | Modify | Add 4 helpers. |
| `src/app/modules/ai/service.py` | Modify | Real `list_steps`, `list_policy_calls`. |
| `tests/modules/ai/test_service_lists.py` | Create | Pagination + order tests. |

## Edge Cases & Risks
- Empty result for a valid run id must return `([], 0)` — NOT a 404.
- DTO layer expects `camelCase` aliases on dump; validate round-trip.

## Acceptance Verification
- [ ] Pagination honored.
- [ ] Orders correct (steps ASC by number; policy calls ASC by created_at).
- [ ] Unknown run id → 404.
- [ ] Empty run id (valid but no rows) → empty list + total=0.
