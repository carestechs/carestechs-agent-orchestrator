# Implementation Plan: T-109 — `TaskAssignment` entity

## Task Reference
- **Task ID:** T-109
- **Type:** Database
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-108

## Overview
Append-only assignment history for tasks, enforced by a partial-unique index so at most one row per task has `superseded_at IS NULL`.

## Steps

### 1. Modify `src/app/modules/ai/schemas.py`
- Enum:
  ```python
  class AssigneeType(StrEnum): DEV="dev"; AGENT="agent"
  ```
- `TaskAssignmentDto` per data-model §TaskAssignment.

### 2. Modify `src/app/modules/ai/models.py`
- `TaskAssignment(Base)` columns per data-model doc.
- FK: `task_id → tasks.id ON DELETE RESTRICT`.
- Check constraint on `assignee_type`.
- Indexes:
  ```python
  Index("ix_task_assignments_active", "task_id", unique=True, postgresql_where=text("superseded_at IS NULL"))
  Index("ix_task_assignments_task_assigned", "task_id", text("assigned_at DESC"))
  ```

### 3. Create `src/app/migrations/versions/<ts>_add_task_assignments.py`
- Autogenerate. The partial index requires a manual edit of the migration to emit the correct `postgresql_where` (Alembic autogenerate sometimes drops this).

### 4. Modify `tests/modules/ai/test_models.py`
- `TestTaskAssignment`:
  - Happy-path insert.
  - Attempt to insert a second `superseded_at IS NULL` row → `IntegrityError`.
  - Superseding pattern: update prior row `superseded_at=now()`, then insert new row — succeeds.

### 5. Modify `tests/conftest.py`
- Cleanup: delete `task_assignments` before `tasks`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/schemas.py` | Modify | `AssigneeType` + `TaskAssignmentDto`. |
| `src/app/modules/ai/models.py` | Modify | New `TaskAssignment` class. |
| `src/app/migrations/versions/<ts>_add_task_assignments.py` | Create | Migration with partial-unique. |
| `tests/modules/ai/test_models.py` | Modify | Extend. |
| `tests/conftest.py` | Modify | Cleanup order. |

## Edge Cases & Risks
- **Autogenerate omits `postgresql_where`** — verify the partial index in the migration manually. If missing, add it by hand.
- **Race on reassign** — superseding must happen in the same transaction as inserting the new row (T-116 enforces this). Plan doesn't cover it here; test in T-116 or T-122.

## Acceptance Verification
- [ ] Model matches data-model.
- [ ] Partial-unique index rejects a second active assignment.
- [ ] BTREE on `(task_id, assigned_at DESC)`.
- [ ] Migration round-trips with the partial index intact.
- [ ] `uv run pyright`, `ruff`, tests green.
