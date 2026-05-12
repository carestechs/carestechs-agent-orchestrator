# Implementation Plan: T-286 — Executor swap: `_handle_request_work_item_load` reads from DB

## Task Reference
- **Task ID:** T-286
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** AC-5. The moment this lands, the orchestrator stops needing filesystem access to brief paths. AC-6 follows by extension once the CLI flag in T-289 lands.

## Overview
Flip `_handle_request_work_item_load` from `ctx.intake["workItemPath"]` + `Path.read_text()` to a DB lookup keyed on `intake.workItem.id` (with a legacy fallback to `workItemId`). Returns the body in the memory patch under the existing key downstream nodes expect. No more `pathlib` import in this executor.

## Implementation Steps

### Step 1: Add `_load_work_item_body` helper
**File:** `src/app/modules/ai/executors/bootstrap.py`
**Action:** Modify
Just above `_handle_request_work_item_load`:
```python
async def _load_work_item_body(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    external_ref: str,
) -> tuple[str | None, str | None]:
    """Return (body_md, source_path) for the work item. Either may be None."""
    async with session_factory() as session:
        row = await session.scalar(
            select(WorkItem).where(WorkItem.external_ref == external_ref)
        )
        if row is None:
            return None, None
        return row.body_md, row.source_path
```
Short-lived session per call (CLAUDE.md: "Don't share memory across runs", and the runtime loop pattern of one-session-per-iteration). Returns both fields so the legacy fallback in T-288 can use `source_path` without re-querying.

### Step 2: Rewrite `_handle_request_work_item_load`
**File:** `src/app/modules/ai/executors/bootstrap.py`
**Action:** Modify
Current body reads `ctx.intake.get("workItemPath")` and stuffs it in memory. New body:
```python
async def _handle_request_work_item_load(ctx: DispatchContext) -> Mapping[str, Any]:
    work_item = ctx.intake.get("workItem")
    if isinstance(work_item, dict) and work_item.get("id"):
        external_ref = work_item["id"]
    else:
        # Legacy fallback wired in T-288 — for now, surface the missing-id case.
        external_ref = ctx.intake.get("workItemId")
        if not external_ref:
            return {
                "loaded": False,
                "error": "work-item-not-present-in-intake",
                "__memory_patch": {"work_item_body": None, "work_item_path": None},
            }

    body_md, source_path = await _load_work_item_body(
        ctx.session_factory, external_ref=external_ref
    )
    return {
        "loaded": body_md is not None,
        "externalRef": external_ref,
        "__memory_patch": {
            "work_item_body": body_md,
            "work_item_path": source_path,  # legacy compat for any downstream that still reads it
            "work_item_id": external_ref,
        },
    }
```
**Important:** `DispatchContext.session_factory` — confirm this attribute exists by reading `executors/base.py`; if not, plumb it through `register_all_executors` (one-line change at the executor construction site).

### Step 3: Verify the memory-patch key matches downstream expectations
**File:** `src/app/modules/ai/executors/bootstrap.py` (read-only audit) + `src/app/modules/ai/executors/propose_tasks.py` (read-only audit)
**Action:** Read
Grep the codebase: `grep -rn "work_item_path\|workItemPath\|work_item_body" src/app/`. Map every read site to confirm `work_item_body` is the new canonical key. Any site still reading `work_item_path` keeps working because we set both — but flag T-287 to clean up.

### Step 4: Unit tests
**File:** `tests/modules/ai/test_executor_load_work_item.py`
**Action:** Create
Real-Postgres fixture. Tests:
1. `test_load_from_db_returns_body_in_memory_patch` — INSERT a `WorkItem` with `body_md="# Test\nbody"`, run executor, assert memory patch has `work_item_body == "# Test\nbody"` and no file IO.
2. `test_missing_work_item_returns_loaded_false` — `WorkItem` not present, executor returns `loaded=False` without crashing.
3. `test_no_id_in_intake_returns_loaded_false_with_error` — intake missing both `workItem` and `workItemId`.
4. `test_session_discipline` — assert the executor opens and closes its session per call (use a `session_factory` that counts opens/closes).
5. `test_no_pathlib_open_in_load_path` — wrap `pathlib.Path.read_text` with a mock that fails the test; run the executor; assert it wasn't called.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/executors/bootstrap.py` | Modify | New helper + executor body |
| `src/app/modules/ai/executors/base.py` | Modify (conditional) | Add `session_factory` to `DispatchContext` if missing |
| `tests/modules/ai/test_executor_load_work_item.py` | Create | Five tests |

## Edge Cases & Risks
- **`DispatchContext.session_factory` may not exist.** If `ctx` doesn't carry a session factory today, plumb it through `LocalExecutor` construction — a one-line addition. Read `executors/base.py` and `executors/local.py` first. **This is the highest-risk step** because it can ripple into every executor's construction site.
- **Memory-patch key migration.** Setting both `work_item_body` (new) and `work_item_path` (legacy) means downstream consumers keep working without coordinated changes. T-287 cleans up the legacy key once verified.
- **`run.intake` mutability.** The executor reads `ctx.intake` which is a dict snapshot of the run's intake. T-285 seeded `engineItemId` from the work-item row, so the engine executor's existing read path still works.

## Acceptance Verification
- [ ] All five unit tests pass.
- [ ] `grep -n "Path" src/app/modules/ai/executors/bootstrap.py | grep -i "work.*item"` returns nothing (no pathlib in the work-item load path).
- [ ] An end-to-end-ish test where the executor runs against a DB-only `WorkItem` (no disk file) succeeds and feeds `work_item_body` to downstream nodes.
- [ ] `_load_work_item_body` opens exactly one session per call, closes it before returning (verified by a counting `async_sessionmaker` wrapper).
