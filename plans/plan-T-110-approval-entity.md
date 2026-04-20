# Implementation Plan: T-110 — `Approval` entity

## Task Reference
- **Task ID:** T-110
- **Type:** Database
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-108

## Overview
Append-only record of every approve/reject decision on a task. Introduces `ApprovalStage`, `ApprovalDecision`, and `ActorRole` enums. Rejection iteration count is derived (count of `decision='reject'` rows); no denormalization.

## Steps

### 1. Modify `src/app/modules/ai/schemas.py`
- Enums:
  ```python
  class ApprovalStage(StrEnum): PROPOSED="proposed"; PLAN="plan"; IMPL="impl"
  class ApprovalDecision(StrEnum): APPROVE="approve"; REJECT="reject"
  class ActorRole(StrEnum): ADMIN="admin"; DEV="dev"
  ```
- `ApprovalDto` per data-model §Approval.

### 2. Modify `src/app/modules/ai/models.py`
- `Approval(Base)` columns per data-model doc.
- FK: `task_id → tasks.id ON DELETE RESTRICT`.
- Check constraints: `stage`, `decision`, `decided_by_role`.
- Index: `ix_approvals_task_stage_time` BTREE on `(task_id, stage, decided_at)`.

### 3. Create `src/app/migrations/versions/<ts>_add_approvals.py`
- Autogenerate, rename, round-trip.

### 4. Modify `tests/modules/ai/test_models.py`
- `TestApproval`: insert round-trip, feedback can be NULL for `approve`, counting `reject` rows for a `(task_id, stage)` pair returns the iteration count.

### 5. Modify `tests/conftest.py`
- Cleanup: delete `approvals` before `tasks`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/schemas.py` | Modify | Three enums + `ApprovalDto`. |
| `src/app/modules/ai/models.py` | Modify | New `Approval` class. |
| `src/app/migrations/versions/<ts>_add_approvals.py` | Create | Migration. |
| `tests/modules/ai/test_models.py` | Modify | Extend. |
| `tests/conftest.py` | Modify | Cleanup order. |

## Edge Cases & Risks
- **`feedback` non-empty on reject** — enforced at service layer (T-113), not in the DB. The model accepts `Optional[str]` for simplicity.
- **`ActorRole` vs. `ActorType`** — keep distinct even though `admin` overlaps. `ActorType` is for proposers (admin/agent); `ActorRole` is for approvers (admin/dev, always human). Do not fuse.

## Acceptance Verification
- [ ] `Approval` fields match data-model.
- [ ] BTREE on `(task_id, stage, decided_at)`.
- [ ] Check constraints on `stage`, `decision`, `decided_by_role`.
- [ ] Migration round-trips.
- [ ] `ApprovalDto` uses camelCase + `extra="forbid"`.
- [ ] `uv run pyright`, `ruff`, tests green.
