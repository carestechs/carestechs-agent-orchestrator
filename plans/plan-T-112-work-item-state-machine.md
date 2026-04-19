# Implementation Plan: T-112 — Work-item state-machine service + W2/W5 derivation

## Task Reference
- **Task ID:** T-112
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-107, T-108

## Overview
Service-layer functions that own every work-item transition. Illegal transitions raise `ConflictError` (RFC 7807 `409`). W2 and W5 are the derived transitions — fire idempotently from orchestrator code after parent signals commit.

## Steps

### 1. Create `src/app/modules/ai/lifecycle/__init__.py`
- Empty file declaring the submodule.

### 2. Create `src/app/modules/ai/lifecycle/work_items.py`
- Module-level transition functions:
  - `async def open_work_item(session, *, external_ref, type, title, source_path, opened_by) -> WorkItem` — W1.
  - `async def lock_work_item(session, work_item_id, *, actor) -> WorkItem` — W3. Only valid from `in_progress`; else `ConflictError`. Sets `locked_from=in_progress`.
  - `async def unlock_work_item(session, work_item_id, *, actor) -> WorkItem` — W4. Only valid from `locked`; clears `locked_from`.
  - `async def close_work_item(session, work_item_id, *, actor) -> WorkItem` — W6. Only valid from `ready`; sets `closed_at`, `closed_by`.
  - `async def maybe_advance_to_in_progress(session, work_item_id) -> bool` — W2. Locks the row (`SELECT ... FOR UPDATE`); advances only if `status=='open'`; idempotent (returns `False` if already `in_progress`).
  - `async def maybe_advance_to_ready(session, work_item_id) -> bool` — W5. Locks the row; checks that `count(tasks) >= 1` AND every task has `status IN ('done','deferred')`. Returns `False` if not eligible.
- All functions use `async with session.begin():` for tight transaction scope.
- Raise `ConflictError(code="work-item-transition-forbidden", detail=...)` with source+target states on illegal transitions.

### 3. Modify `src/app/core/exceptions.py`
- If `ConflictError` lacks a stable code helper, add one. Otherwise reuse existing.

### 4. Create `tests/modules/ai/lifecycle/__init__.py`
- Empty.

### 5. Create `tests/modules/ai/lifecycle/test_work_items.py`
- One test per happy-path transition (W1, W3, W4, W6, W2, W5).
- Illegal-transition tests: lock from `open` → `409`; unlock from `in_progress` → `409`; close from `in_progress` → `409`.
- Derivation idempotency: call `maybe_advance_to_in_progress` twice — second returns `False`, state unchanged.
- W5 edge case: zero-task work item → `maybe_advance_to_ready` returns `False`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/lifecycle/__init__.py` | Create | New submodule. |
| `src/app/modules/ai/lifecycle/work_items.py` | Create | Transition + derivation functions. |
| `tests/modules/ai/lifecycle/__init__.py` | Create | Test package. |
| `tests/modules/ai/lifecycle/test_work_items.py` | Create | Unit tests. |

## Edge Cases & Risks
- **Concurrent approvals on first task** — two `/approve` calls in flight could both try to advance W2. `SELECT ... FOR UPDATE` serializes them; second call sees `status=in_progress` and returns `False`. Test this with a `pytest.mark.asyncio.gather` scenario if feasible; otherwise flag for T-122.
- **`maybe_advance_to_ready` race with late defer** — if a defer arrives between the count check and the status check, no issue since we hold the row lock. But tasks are not locked; a task could theoretically flip from `implementing → done` mid-scan. Acceptable: W5 is idempotent; next call picks it up.

## Acceptance Verification
- [ ] 6 explicit transitions + 2 derived implemented.
- [ ] `SELECT ... FOR UPDATE` on the work-item row in each.
- [ ] Illegal transitions → `ConflictError` / `409`.
- [ ] Derivation idempotent.
- [ ] Zero-task work item does not advance to `ready`.
- [ ] `uv run pyright`, `ruff`, unit tests green.
