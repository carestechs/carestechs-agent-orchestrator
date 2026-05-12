# Implementation Plan: T-285 — Route wiring + RFC 7807 errors + payload size cap

## Task Reference
- **Task ID:** T-285
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** AC-1..AC-4 become observable through the public API. The 413 cap closes the "very large brief" edge case from Section 9 of the brief.

## Overview
Wire `register_work_item` (T-284) into `POST /api/v1/runs`. Map the three typed exceptions to Problem Details responses with diff metadata. Enforce a server-side body size cap before sha256-hashing so a malicious upload doesn't consume CPU before validation.

## Implementation Steps

### Step 1: Config — `intake_work_item_max_bytes`
**File:** `src/app/config.py`
**Action:** Modify
Add to `Settings`:
```python
intake_work_item_max_bytes: int = Field(default=1_048_576, ge=1)  # 1 MB
```
Per CLAUDE.md: pydantic-settings, env-var name `INTAKE_WORK_ITEM_MAX_BYTES` (uppercase prefix).

### Step 2: Pre-validate body size before registration
**File:** `src/app/modules/ai/router.py`
**Action:** Modify
At the top of the run-start handler (after request validation, before any DB work), check:
```python
if request.intake.work_item and request.intake.work_item.content:
    if len(request.intake.work_item.content.encode("utf-8")) > settings.intake_work_item_max_bytes:
        raise PayloadTooLargeError(...)  # mapped to 413
```
Add `PayloadTooLargeError(AppError)` in `exceptions.py` with `code="payload-too-large"`, `http_status=413` (if not already present). Confirm by reading the file first.

### Step 3: Call `register_work_item` inside the run-start transaction
**File:** `src/app/modules/ai/router.py`
**Action:** Modify
Inside the existing `async with db.begin():` (or equivalent — read first to confirm shape):
```python
work_item: WorkItem | None = None
if request.intake.work_item is not None:
    work_item = await register_work_item(db, request.intake.work_item)

# ... existing Run creation, linking work_item.id if present
run = Run(..., intake=request.intake.model_dump(by_alias=True), ...)
db.add(run)
```
The 400 / 409 / 409 exceptions propagate to the global handler — no try/except here.

If `work_item` is non-None, also seed `Run.intake["engineItemId"]` from `work_item.engine_item_id` when present (preserves the existing executor expectations without forcing T-287 to land first).

### Step 4: Update global exception handler for diff metadata
**File:** `src/app/core/exceptions.py`
**Action:** Modify
The existing `AppError → Problem Details` handler builds the response body. Extend it (or specialize for the two conflict classes) so the response includes:
```json
{
  "type": "...problems/work-item-content-conflict",
  "title": "Work item content conflict",
  "status": 409,
  "detail": "...",
  "meta": {
    "storedSha256": "abcd...",
    "uploadedSha256": "ef01...",
    "code": "work-item-content-conflict"
  }
}
```
Same for `WorkItemKindConflictError` with `storedKind` / `uploadedKind`. Per CLAUDE.md, `meta` field naming is camelCase.

### Step 5: Integration tests
**File:** `tests/integration/test_runs_route_work_item_upload.py`
**Action:** Create
Real-Postgres, FastAPI `AsyncClient`. Tests:
1. `test_post_run_first_sight_inserts_work_item_and_starts_run` (AC-1).
2. `test_post_run_second_sight_reuses_existing_row` (AC-2).
3. `test_post_run_content_conflict_returns_409_with_diff` (AC-3) — asserts response body has `meta.storedSha256` and `meta.uploadedSha256`.
4. `test_post_run_no_row_no_content_returns_400` (AC-4) — asserts `type` ends with `work-item-not-registered`.
5. `test_post_run_oversized_content_returns_413` — override `INTAKE_WORK_ITEM_MAX_BYTES=128` via env, POST 200-byte content, assert 413 *before* any DB write (check `SELECT COUNT FROM work_items` is unchanged).
6. `test_conflict_does_not_leave_partial_run` — POST that triggers 409; assert no `Run` row exists for the request id.

### Step 6: docs/api-spec.md changes
**File:** `docs/api-spec.md`
**Action:** Modify
Stub update — full sweep in T-294, but the route's new errors land here so reviewers can find them:
- Document `intake.workItem` request shape.
- Add the three new error codes (`work-item-not-registered`, `work-item-content-conflict`, `work-item-kind-conflict`) plus `payload-too-large` to the Problem Details catalog.
- Add a one-line changelog entry referencing FEAT-014.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/config.py` | Modify | `intake_work_item_max_bytes` setting |
| `src/app/core/exceptions.py` | Modify | Handler extension for diff metadata; `PayloadTooLargeError` if missing |
| `src/app/modules/ai/router.py` | Modify | Wire registration + size cap; seed `engineItemId` from row |
| `tests/integration/test_runs_route_work_item_upload.py` | Create | Six AC-coverage tests |
| `docs/api-spec.md` | Modify | Error codes + intake shape stub |

## Edge Cases & Risks
- **Size cap measures bytes, not chars.** Important for non-ASCII content. Use `len(content.encode("utf-8"))`, not `len(content)`.
- **Transaction scope.** The `register_work_item` call shares the `Run` creation transaction so a 409 raised after partial work doesn't leave a half-created run (verified in test #6). If the existing route splits transactions, fold them.
- **409 vs 422.** RFC 7807 / FastAPI convention: 422 is for request *parsing* failures; 409 is the right code here (state conflict against an existing resource). Tests assert 409 explicitly.
- **`request.intake.model_extra` carries legacy keys** (`workItemPath`, etc.). T-288 reads them; this task does not.

## Acceptance Verification
- [ ] Six integration tests pass against a real Postgres.
- [ ] Test #5 (413) demonstrates the cap fires *before* DB I/O — `SELECT COUNT` unchanged.
- [ ] Test #6 confirms transactional rollback on 409.
- [ ] Conflict bodies include `meta.storedSha256` / `meta.uploadedSha256` / `meta.storedKind` / `meta.uploadedKind`.
- [ ] `docs/api-spec.md` stub mentions the three new error codes + 413.
