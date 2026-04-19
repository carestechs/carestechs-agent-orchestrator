# Implementation Plan: T-107 — `WorkItem` entity

## Task Reference
- **Task ID:** T-107
- **Type:** Database
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** None

## Overview
Add the `WorkItem` SQLAlchemy model, Alembic migration, `WorkItemStatus` / `WorkItemType` enums, and `WorkItemDto`. First of four Foundation tables; unlocks every FEAT-006 task that references a work item.

## Steps

### 1. Modify `src/app/modules/ai/schemas.py`
- Add enums (`StrEnum` subclasses, lowercase snake_case values):
  ```python
  class WorkItemType(StrEnum): FEAT = "FEAT"; BUG = "BUG"; IMP = "IMP"
  class WorkItemStatus(StrEnum): OPEN="open"; IN_PROGRESS="in_progress"; LOCKED="locked"; READY="ready"; CLOSED="closed"
  ```
- Add `WorkItemDto(BaseModel)` with `model_config = _CAMEL_CONFIG` + `extra="forbid"`. Fields mirror the data-model entity table.

### 2. Modify `src/app/modules/ai/models.py`
- Add `WorkItem(Base)` with columns per `docs/data-model.md` §WorkItem.
- Primary key: `id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)` following the `Run` pattern.
- Check constraints on `type`, `status`, `locked_from` built via `CheckConstraint(...)` in `__table_args__` using the enum values.
- Named indexes: `uq_work_items_external_ref` (UNIQUE on `external_ref`), `ix_work_items_status_updated_at` BTREE.
- `created_at` / `updated_at` use `server_default=func.now()`; `updated_at` sets `onupdate=func.now()`.

### 3. Create `src/app/migrations/versions/<ts>_add_work_items.py`
- Generate via `uv run alembic revision --autogenerate -m "add work_items"`.
- Rename to descriptive slug per CLAUDE.md (`YYYY_MM_DD_add_work_items.py`).
- Verify `upgrade` + `downgrade` round-trip on a local Postgres.

### 4. Modify `tests/modules/ai/test_models.py`
- New test class `TestWorkItem`: insert/select round-trip, unique `external_ref` violation, check-constraint rejection (bad status), `updated_at` bumps on UPDATE.

### 5. Modify `tests/conftest.py`
- Extend `_cleanup_rows` fixture to delete `work_items` before `runs` (no FK yet — cleanup order still matters for later tasks).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/schemas.py` | Modify | New enums + `WorkItemDto`. |
| `src/app/modules/ai/models.py` | Modify | New `WorkItem` class. |
| `src/app/migrations/versions/<ts>_add_work_items.py` | Create | Alembic migration. |
| `tests/modules/ai/test_models.py` | Modify | Round-trip + constraint tests. |
| `tests/conftest.py` | Modify | Cleanup order. |

## Edge Cases & Risks
- **UUIDv7 collision** — not a real risk; documented via the `uuid7` helper used elsewhere.
- **Locked_from nullability** — column is text-nullable; check constraint must allow NULL (`locked_from IS NULL OR locked_from IN (...)`).
- **Migration ordering** — other foundation migrations (T-108/T-109/T-110/T-111) land after; verify `alembic heads` shows a single head after all land.

## Acceptance Verification
- [ ] SQLAlchemy model fields match data-model doc.
- [ ] UNIQUE on `external_ref`, BTREE on `(status, updated_at DESC)`, named.
- [ ] Check constraints on `status`, `type`, `locked_from`.
- [ ] Migration round-trips cleanly.
- [ ] `WorkItemDto` forbids extras, uses camelCase aliases.
- [ ] `uv run pyright`, `ruff`, model tests green.
