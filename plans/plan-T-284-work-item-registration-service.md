# Implementation Plan: T-284 — `register_work_item` service (INSERT-or-reuse-or-409)

## Task Reference
- **Task ID:** T-284
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** AC-1..AC-4 directly. The single chokepoint that enforces "briefs are immutable" — keeping the logic in one function shrinks the test surface and makes the immutability rule auditable in one place.

## Overview
Pure async service function that takes a parsed `RunIntakeWorkItem` and returns a `WorkItem` row, applying the seven-branch state machine: `(row, content) × (no, yes, match, mismatch, kind-mismatch)`. New typed exceptions in `app/core/exceptions.py` translate to 400 / 409 at the route layer.

## Implementation Steps

### Step 1: Add typed exceptions
**File:** `src/app/core/exceptions.py`
**Action:** Modify
After existing exception classes, add three subclasses of `AppError`:
```python
class WorkItemNotRegisteredError(AppError):
    code = "work-item-not-registered"
    http_status = 400

class WorkItemContentConflictError(AppError):
    code = "work-item-content-conflict"
    http_status = 409
    def __init__(self, *, stored_sha256: str, uploaded_sha256: str) -> None:
        super().__init__(f"work-item body sha256 mismatch: stored={stored_sha256[:12]} uploaded={uploaded_sha256[:12]}")
        self.stored_sha256 = stored_sha256
        self.uploaded_sha256 = uploaded_sha256

class WorkItemKindConflictError(AppError):
    code = "work-item-kind-conflict"
    http_status = 409
    def __init__(self, *, stored_kind: str, uploaded_kind: str) -> None:
        super().__init__(f"work-item kind mismatch: stored={stored_kind} uploaded={uploaded_kind}")
        self.stored_kind = stored_kind
        self.uploaded_kind = uploaded_kind
```
Update the global handler (existing) to map these exception classes to RFC 7807 Problem Details with their `code` as the `type` URI tail and the extra fields in `meta`.

### Step 2: Add `_derive_title` helper
**File:** `src/app/modules/ai/lifecycle/work_item_registry.py` (new module — keeps `service.py` from growing)
**Action:** Create
```python
def _derive_title(body_md: str, *, fallback: str) -> str:
    """Return the first H1 line, stripped, or `fallback` if none."""
    for line in body_md.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and len(stripped) > 2:
            return stripped[2:].strip()
    return fallback
```
Test coverage: leading whitespace, no H1, multi-line content, only H2 present.

### Step 3: Implement `register_work_item`
**File:** `src/app/modules/ai/lifecycle/work_item_registry.py`
**Action:** Create
```python
async def register_work_item(
    session: AsyncSession,
    dto: RunIntakeWorkItem,
    *,
    opened_by: str = "upload",
) -> WorkItem:
    incoming_sha = (
        hashlib.sha256(dto.content.encode("utf-8")).hexdigest()
        if dto.content is not None
        else None
    )

    existing = await session.scalar(
        select(WorkItem).where(WorkItem.external_ref == dto.id)
    )

    if existing is None:
        if incoming_sha is None:
            raise WorkItemNotRegisteredError(f"work-item not registered: {dto.id}")
        wi = WorkItem(
            external_ref=dto.id,
            type=dto.kind.value,
            title=_derive_title(dto.content or "", fallback=dto.id),
            body_md=dto.content,
            body_sha256=incoming_sha,
            opened_by=opened_by,
            status=WorkItemStatus.OPEN.value,
        )
        session.add(wi)
        try:
            await session.flush()
        except IntegrityError:
            # Concurrent INSERT — re-read.
            await session.rollback()
            existing = await session.scalar(
                select(WorkItem).where(WorkItem.external_ref == dto.id)
            )
            assert existing is not None  # the IntegrityError implies it exists now
            return await _reconcile(session, existing, dto, incoming_sha)
        return wi

    return await _reconcile(session, existing, dto, incoming_sha)


async def _reconcile(
    session: AsyncSession,
    row: WorkItem,
    dto: RunIntakeWorkItem,
    incoming_sha: str | None,
) -> WorkItem:
    if row.type != dto.kind.value:
        raise WorkItemKindConflictError(stored_kind=row.type, uploaded_kind=dto.kind.value)
    if incoming_sha is None:
        return row
    if row.body_sha256 is None:
        # Pre-FEAT-014 row — backfill.
        row.body_md = dto.content
        row.body_sha256 = incoming_sha
        return row
    if row.body_sha256 != incoming_sha:
        raise WorkItemContentConflictError(
            stored_sha256=row.body_sha256, uploaded_sha256=incoming_sha
        )
    return row
```
Follow CLAUDE.md: returns a real model (never `dict`); all I/O is `async def`; no SDK imports beyond stdlib.

### Step 4: Unit tests
**File:** `tests/modules/ai/test_work_item_registry.py`
**Action:** Create
Real-Postgres fixture. Tests:
1. `test_first_sight_inserts_row` — no row + content → INSERT.
2. `test_no_content_no_row_raises` — no row + no content → `WorkItemNotRegisteredError`.
3. `test_no_content_with_row_reuses` — row exists, dto has no content → returns row.
4. `test_matching_content_returns_existing` — row exists with sha; dto has same sha → returns row (no UPDATE).
5. `test_mismatched_content_raises` — `WorkItemContentConflictError` with the diff in attributes.
6. `test_kind_mismatch_raises` — row.type=FEAT, dto.kind=BUG → `WorkItemKindConflictError`.
7. `test_backfills_null_sha256` — row with NULL body fields gets backfilled.
8. `test_concurrent_first_sight_race` — `asyncio.gather` two `register_work_item` calls with identical content; one inserts, the other re-reads via the IntegrityError branch; no exceptions; only one row in DB.
9. `test_sha256_no_normalization_crlf_vs_lf` — same logical text with CRLF vs LF produces different sha and triggers 409.

### Step 5: `_derive_title` tests
**File:** `tests/modules/ai/test_work_item_registry.py`
**Action:** Modify
Tests in the same file (since it's a private helper):
- H1 at top → returns title.
- H1 after a leading blank line → returns title.
- Only H2 present → returns fallback.
- No headings → returns fallback.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/core/exceptions.py` | Modify | Three new typed exceptions |
| `src/app/modules/ai/lifecycle/work_item_registry.py` | Create | `register_work_item`, `_reconcile`, `_derive_title` |
| `tests/modules/ai/test_work_item_registry.py` | Create | Nine functional tests + four title tests |

## Edge Cases & Risks
- **Concurrent INSERT race.** Postgres UNIQUE handles the race; the IntegrityError → rollback → re-read path is the only correct sequence. Tested explicitly in step 4 #8.
- **`session.flush()` vs implicit commit.** The route opens the transaction; this function uses `flush()` so the route's commit/rollback discipline is preserved (matches FEAT-008 reactor pattern).
- **CRLF vs LF.** Documented in the brief Section 9. Test #9 *asserts* this divergence — if a future refactor adds normalization, the test will fail and the conversation re-opens.
- **`opened_by="upload"` default.** Future remote agents may want to pass an actor identity here. Parameter is keyword-only for forward compat.

## Acceptance Verification
- [ ] All nine functional tests + four title tests pass.
- [ ] Pyright strict clean (no `Any` in the function body).
- [ ] `grep -rn "open(" src/app/modules/ai/lifecycle/work_item_registry.py` returns nothing — no filesystem reads.
- [ ] Concurrent-race test holds up under three repeated runs (`uv run pytest -- --count=3`).
- [ ] CRLF-vs-LF test produces a 409 — codifies the no-normalization rule.
