# Implementation Plan: T-108 — `Task` entity

## Task Reference
- **Task ID:** T-108
- **Type:** Database
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-107

## Overview
Add the `Task` SQLAlchemy model, migration, `TaskStatus` / `ActorType` enums, and `TaskDto`. Second foundation table; carries the main state machine and is referenced by `TaskAssignment` (T-109) and `Approval` (T-110).

## Steps

### 1. Modify `src/app/modules/ai/schemas.py`
- Enums:
  ```python
  class TaskStatus(StrEnum):
      PROPOSED="proposed"; APPROVED="approved"; ASSIGNING="assigning"; PLANNING="planning"
      PLAN_REVIEW="plan_review"; IMPLEMENTING="implementing"; IMPL_REVIEW="impl_review"
      DONE="done"; DEFERRED="deferred"
  class ActorType(StrEnum): ADMIN="admin"; AGENT="agent"
  ```
- `TaskDto` with all fields from data-model doc plus optional `currentAssignment: TaskAssignmentDto | None = None` (populated by service; T-109 defines `TaskAssignmentDto`).

### 2. Modify `src/app/modules/ai/models.py`
- `Task(Base)` columns per data-model §Task.
- FK: `work_item_id: Mapped[UUID] = mapped_column(ForeignKey("work_items.id", ondelete="RESTRICT"), nullable=False)`.
- Check constraints on `status` (all 9 values), `proposer_type`, and nullable `deferred_from`.
- Indexes: `uq_tasks_work_item_ref` UNIQUE on `(work_item_id, external_ref)`, `ix_tasks_work_item_status` BTREE on `(work_item_id, status)`.

### 3. Create `src/app/migrations/versions/<ts>_add_tasks.py`
- Autogenerate, rename with descriptive slug, verify round-trip.

### 4. Modify `tests/modules/ai/test_models.py`
- `TestTask`: insert + FK cascade behavior (RESTRICT), UNIQUE `(work_item_id, external_ref)` violation, each status value accepted, `deferred_from` nullable.

### 5. Modify `tests/conftest.py`
- `_cleanup_rows`: delete `tasks` before `work_items`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/schemas.py` | Modify | Enums + `TaskDto`. |
| `src/app/modules/ai/models.py` | Modify | New `Task` class. |
| `src/app/migrations/versions/<ts>_add_tasks.py` | Create | Migration. |
| `tests/modules/ai/test_models.py` | Modify | Extend. |
| `tests/conftest.py` | Modify | Cleanup order. |

## Edge Cases & Risks
- **`currentAssignment` as computed DTO field** — not a DB column; service populates it. Don't add a FK on Task for "current assignment" (would fight the append-only TaskAssignment model per the task's Technical Notes).
- **Cross-work-item external_ref** — `T-042` can exist under multiple work items; that's why the UNIQUE is composite.
- **ON DELETE RESTRICT** — work items are never deleted in v1, but the constraint guards against future footguns.

## Acceptance Verification
- [ ] `Task` fields match data-model doc.
- [ ] UNIQUE on `(work_item_id, external_ref)`, BTREE on `(work_item_id, status)`.
- [ ] Check constraint covers all 9 status values.
- [ ] FK with `ON DELETE RESTRICT`.
- [ ] Migration round-trips.
- [ ] `TaskDto` with `extra="forbid"` + camelCase + `currentAssignment` optional.
- [ ] `uv run pyright`, `ruff`, tests green.
