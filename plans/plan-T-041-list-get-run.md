# Implementation Plan: T-041 — `list_runs` + `get_run` with filters and last-step summary

## Task Reference
- **Task ID:** T-041
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-039

## Overview
Read-side of the run lifecycle: paginated list with `status` + `agentRef` filters, and a single-run detail with the latest step summary. Thin query helpers live in `repository.py` to keep `service.py` readable.

## Steps

### 1. Create `src/app/modules/ai/repository.py`
Thin typed query helpers:
- `async def count_runs(db, *, status, agent_ref) -> int`.
- `async def select_runs(db, *, status, agent_ref, page, page_size) -> list[Run]`: ORDER BY `started_at DESC, id DESC` (stable tiebreaker); LIMIT/OFFSET.
- `async def get_run_by_id(db, run_id) -> Run | None`.
- `async def latest_step(db, run_id) -> Step | None`: SELECT … ORDER BY `step_number DESC` LIMIT 1.
- `async def count_steps(db, run_id) -> int` — used by `get_run` detail.
Helpers are `async`, take `AsyncSession`, and return ORM rows (the service layer adapts to DTOs).

### 2. Modify `src/app/modules/ai/service.py`
Replace `list_runs` body:
- Clamp `page` ≥ 1, `page_size` ∈ [1, 100].
- `total = await repository.count_runs(db, status=..., agent_ref=...)`.
- `rows = await repository.select_runs(...)`.
- Return `([RunSummaryDto.model_validate(r, from_attributes=True) for r in rows], total)`.

Replace `get_run` body:
- `run = await repository.get_run_by_id(db, run_id)` → `NotFoundError` if None.
- `step_count = await repository.count_steps(db, run_id)`.
- `last_step = await repository.latest_step(db, run_id)` — may be None.
- Build `last_step_dto = LastStepSummary.model_validate(last_step, from_attributes=True) if last_step else None`.
- Return `RunDetailDto(..., step_count=step_count, last_step=last_step_dto)`.

### 3. Create `tests/modules/ai/test_service_list_get.py`
- Seed 3 Runs across 2 agent refs, mixed statuses.
- Assertions:
  - No filter: returns all 3 sorted by `started_at DESC`.
  - Filter by `status=pending` returns subset.
  - Filter by `agent_ref` returns subset.
  - Combined filter AND-logic.
  - Pagination: `page_size=1`, iterate `page=1..3` returns all runs exactly once.
  - `total` accurate under filter.
  - `get_run` unknown id → `NotFoundError`.
  - `get_run` with zero steps → `last_step is None`, `step_count == 0`.
  - `get_run` with steps → `last_step.step_number` equals max.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/repository.py` | Create | Thin query helpers. |
| `src/app/modules/ai/service.py` | Modify | Real `list_runs`, `get_run`. |
| `tests/modules/ai/test_service_list_get.py` | Create | Happy + filter + pagination tests. |

## Edge Cases & Risks
- Page beyond total: returns empty list + correct `total` (not an error).
- Large page-size values clamped to 100; invalid combos already rejected at the DTO boundary (FEAT-001 `Query(ge=1, le=100)`).
- `last_step` query should NOT trigger the `lazy="raise"` relationship — use an explicit query, not `run.steps`.

## Acceptance Verification
- [ ] Filters AND together correctly.
- [ ] Pagination `meta.totalCount` accurate.
- [ ] Unknown run id → 404 via `NotFoundError`.
- [ ] `last_step` populated or `null` depending on step count.
- [ ] Route tests (T-060) pass end-to-end against these services.
