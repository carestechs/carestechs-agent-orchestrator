# Implementation Plan: T-131 — Reduce `work_items` + `tasks` tables

## Task Reference
- **Task ID:** T-131
- **Type:** Database
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-129

## Overview
Drop `status`, `locked_from` from `work_items`; drop `status`, `deferred_from` from `tasks`. Add `engine_item_id uuid NOT NULL UNIQUE` to both. Engine becomes the source of truth for state; orchestrator only stores identity and auxiliary data.

## Steps

### 1. Alembic migration — `reduce_work_items_and_tasks`
- `work_items`:
  - `ADD COLUMN engine_item_id uuid` (nullable initially, populated below).
  - For existing rows: generate a fake UUID or require a data migration (for v1 test data, just drop and recreate).
  - `ALTER COLUMN engine_item_id SET NOT NULL; ADD UNIQUE (engine_item_id)`.
  - `DROP COLUMN status, locked_from`.
  - `DROP CHECK ck_status, ck_work_items_locked_from`.
- `tasks`:
  - `ADD COLUMN engine_item_id uuid UNIQUE NOT NULL`.
  - `DROP COLUMN status, deferred_from`.
  - `DROP CHECK ck_status, ck_tasks_deferred_from, ix_tasks_work_item_status`.

### 2. Modify `src/app/modules/ai/models.py`
- Remove `status`, `locked_from` fields + the check constraints from `WorkItem`.
- Add `engine_item_id: Mapped[uuid.UUID] = mapped_column(nullable=False, unique=True)`.
- Same treatment for `Task`: remove `status`, `deferred_from`; add `engine_item_id`.
- The `ix_work_items_status_updated_at` and `ix_tasks_work_item_status` indexes go. If queries need state filtering, that now goes through the engine.

### 3. Modify `src/app/modules/ai/schemas.py`
- `WorkItemDto`: state is now not sourced from the DB row. Two options:
  - (a) Remove `status`, `locked_from` from the DTO.
  - (b) Keep them; the service fills them by querying the engine at read time.
- Recommend (b) for UX continuity; wrap in a `load_work_item_dto(session, engine_client, id)` helper.
- Same for `TaskDto`.

### 4. Modify `tests/modules/ai/test_models.py`
- Remove the tests that assert on `status` / `locked_from` / `deferred_from` existence.
- Add: `test_engine_item_id_unique` — insert two rows with the same `engine_item_id` → IntegrityError.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/models.py` | Modify | Drop state cols, add FK. |
| `src/app/modules/ai/schemas.py` | Modify | DTO reads state from engine. |
| Alembic migration | Create | Schema rework. |
| `tests/modules/ai/test_models.py` | Modify | Update constraint tests. |

## Edge Cases & Risks
- **Existing data on local dev DBs.** Dropping NOT NULL columns with existing rows requires either backfill logic or accepting that dev DBs get a fresh migrate. For v1 prototyping, documenting "drop your dev DB before this migration" in the changelog is acceptable.
- **DTO read cost.** Every read now hits the engine. Cache inside a request context (short TTL) to avoid N+1 on list endpoints.
- **Loss of `status`-based filter indexes.** Previous endpoints that filtered by status (if any) must now filter at the engine. List endpoints will need a different shape.

## Acceptance Verification
- [ ] Migration applies cleanly on an empty DB.
- [ ] `work_items.status` / `locked_from` and `tasks.status` / `deferred_from` columns no longer exist.
- [ ] `engine_item_id` NOT NULL + UNIQUE on both.
- [ ] DTOs load state correctly from the engine via helper.
- [ ] `uv run pyright`, `ruff`, model tests green.
