# Implementation Plan: T-288 — Deprecation shim for legacy `workItemPath`

## Task Reference
- **Task ID:** T-288
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** AC-7 — existing callers (CI, dev scripts, v0.1.0 LLM-policy agent) must keep working through one release.

## Overview
Preserve one minor's backward-compat: if `intake.workItem` is absent but `intake.workItemPath` is present, the route reads the file *server-side* (legacy behavior), then registers the body via `register_work_item` so future requests can dedupe on the row. One WARNING log per run. Removed in the next minor — tagged for grep.

## Implementation Steps

### Step 1: Add deprecation branch to the run-start route
**File:** `src/app/modules/ai/router.py`
**Action:** Modify
Inside the run-start handler, after the new intake-upload branch but before run creation:
```python
if request.intake.work_item is None:
    legacy_path = (request.intake.model_extra or {}).get("workItemPath")
    if legacy_path:
        logger.warning(
            "intake-work-item-path-deprecated",
            extra={"code": "intake-work-item-path-deprecated", "path": legacy_path},
        )
        # DEPRECATED FEAT-014; remove after one minor.
        body = _read_legacy_brief(legacy_path)
        kind, ext_ref = _derive_legacy_kind_and_ref(legacy_path)
        compat_dto = RunIntakeWorkItem(id=ext_ref, kind=kind, content=body)
        work_item = await register_work_item(db, compat_dto, opened_by="legacy-path")
```
Add `_read_legacy_brief` and `_derive_legacy_kind_and_ref` as private helpers in the same module (or in a `compat.py` module if the router file is getting fat).

### Step 2: Helper — `_read_legacy_brief`
**File:** `src/app/modules/ai/router.py` (or `src/app/modules/ai/lifecycle/compat.py`)
**Action:** Create
```python
def _read_legacy_brief(path: str) -> str:
    # DEPRECATED FEAT-014; remove after one minor.
    p = Path(path)
    if not p.is_file():
        raise WorkItemNotRegisteredError(f"legacy workItemPath not found: {path}")
    return p.read_text(encoding="utf-8")


def _derive_legacy_kind_and_ref(path: str) -> tuple[WorkItemType, str]:
    stem = Path(path).stem  # "FEAT-005-lifecycle-agent"
    m = re.match(r"^(FEAT|BUG|IMP)-(\d+)", stem)
    if not m:
        raise WorkItemNotRegisteredError(f"legacy workItemPath has unrecognized filename: {path}")
    kind_str, num = m.groups()
    return WorkItemType(kind_str), f"{kind_str}-{num}"
```
The disk read here is *intentional* — this is the only allowed disk-read site for work-item content, and it's tagged for removal.

### Step 3: Executor-level legacy fallback
**File:** `src/app/modules/ai/executors/bootstrap.py`
**Action:** Modify
In `_handle_request_work_item_load` from T-286, the legacy-fallback branch already returns `loaded=False` when `workItem` is missing. With T-288 in place, the route has already registered the work-item *before* the run started, so the executor's DB read finds the row even for legacy-path runs. **No executor change needed** if T-285's `Run.intake` mirroring populates `workItem.id` from the registered row. Verify by reading T-285's route step 3.

If T-285 doesn't mirror, add one line here:
```python
# Legacy callers: workItemId is populated by the route's compat branch.
external_ref = work_item["id"] if isinstance(work_item, dict) else ctx.intake.get("workItemId")
```

### Step 4: Precedence rule when both keys are present
**File:** `src/app/modules/ai/router.py`
**Action:** Modify
Decided in brief: new wins, legacy is ignored without warning. Implementation:
```python
if request.intake.work_item is not None:
    # New shape — no deprecation log, no legacy read
    work_item = await register_work_item(db, request.intake.work_item)
elif legacy_path := (request.intake.model_extra or {}).get("workItemPath"):
    # ... deprecation branch
```
The `elif` makes the precedence implicit.

### Step 5: Conflict semantics for the compat shim
**File:** `src/app/modules/ai/router.py`
**Action:** Modify
Documented risk from the tasks file: if `register_work_item` raises `WorkItemContentConflictError` from a legacy disk-read body, the request returns 409. This is the right behavior — briefs are immutable — but the response body should include a hint that the path read came from disk:
```python
except WorkItemContentConflictError as e:
    e.add_note(f"legacy workItemPath body diverges from stored brief; consider updating the file at {legacy_path}")
    raise
```
Or surface the same fact via a `meta.legacyPath` field on the Problem Details response.

### Step 6: Integration tests
**File:** `tests/integration/test_runs_route_legacy_work_item_path.py`
**Action:** Create
Tests:
1. `test_legacy_work_item_path_starts_run_and_registers_row` — POST with `workItemPath="docs/work-items/FEAT-005-lifecycle-agent.md"`, assert: 202 response; WARNING log emitted once; `work_items` row exists with `body_md` populated.
2. `test_legacy_path_deprecation_warning_emitted_once_per_run` — verify with `caplog.records` count.
3. `test_legacy_path_with_diverging_disk_content_returns_409` — pre-register a `WorkItem` with a different body via the new path; then POST a legacy `workItemPath` for the same id; assert 409.
4. `test_both_new_and_legacy_keys_uses_new` — POST with both `workItem` and `workItemPath`; assert the new shape wins (no WARNING emitted).
5. `test_legacy_path_missing_file_returns_400` — POST `workItemPath="./does-not-exist.md"`; assert 400 `work-item-not-registered`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/router.py` | Modify | Deprecation branch + helpers + precedence rule |
| `src/app/modules/ai/executors/bootstrap.py` | Modify (cond.) | Fallback line if T-285 mirroring incomplete |
| `tests/integration/test_runs_route_legacy_work_item_path.py` | Create | Five tests |

## Edge Cases & Risks
- **Disk-read at the route layer is a deliberate trade-off.** It's the *only* allowed disk read of a brief, and it's grep-able via `_read_legacy_brief`. The structural guard in T-292 will allow this single call site explicitly.
- **Legacy path resolution is server-side.** The orchestrator process must be able to resolve `workItemPath` against *its own* filesystem. The brief documents this as a transition-only constraint — once `workItem` upload lands, callers should migrate, and the disk-read code goes away in the next minor.
- **`add_note` is Python 3.11+.** Confirm the project's Python target (CLAUDE.md says 3.12+). OK.

## Acceptance Verification
- [ ] All five integration tests pass.
- [ ] `caplog` captures exactly one `code="intake-work-item-path-deprecated"` entry per legacy run.
- [ ] After a legacy run completes, `SELECT body_md FROM work_items WHERE external_ref='FEAT-005'` returns the body.
- [ ] Run with both keys present uses the new shape (test #4).
- [ ] `grep -rn "DEPRECATED FEAT-014" src/app/` lists both `_read_legacy_brief` and the route branch — the future cleanup is one grep away.
