# Implementation Plan: T-117 — Plan endpoints (S8-S10)

## Task Reference
- **Task ID:** T-117
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-113, T-114

## Overview
Three endpoints for the plan stage. Role enforcement is matrix-derived (dev for dev-assigned, admin for agent-assigned) rather than a static dependency. Adds a small `TaskPlan` audit table.

## Steps

### 1. Modify `src/app/modules/ai/models.py`
- New model:
  ```python
  class TaskPlan(Base):
      __tablename__ = "task_plans"
      id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
      task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False)
      plan_path: Mapped[str]
      plan_sha: Mapped[str]
      submitted_by: Mapped[str]
      submitted_at: Mapped[datetime] = mapped_column(server_default=func.now())
  ```
- Index on `(task_id, submitted_at DESC)`.

### 2. Create `src/app/migrations/versions/<ts>_add_task_plans.py`
- Autogenerate + rename.

### 3. Modify `src/app/modules/ai/schemas.py`
- DTOs:
  - `PlanSubmitRequest(planPath: str, planSha: str)`.
  - `PlanApproveRequest()` (empty body).
  - `PlanRejectRequest(feedback: str = Field(min_length=1))`.

### 4. Modify `src/app/modules/ai/service.py`
- Adapters:
  - `submit_plan_signal(session, task_id, req, *, actor, actor_role)`:
    - Loads task + active assignment.
    - For the `/plan` endpoint, role check: dev-assigned → `DEV`; agent-assigned → `ADMIN`.
    - Inserts `TaskPlan` row.
    - Calls `lifecycle.tasks.submit_plan(...)`.
  - `approve_plan_signal(...)` + `reject_plan_signal(...)`:
    - Load task + active assignment inside `SELECT ... FOR UPDATE`.
    - Compute expected role via `approval_matrix(task, assignment, ApprovalStage.PLAN, solo_dev=...)`.
    - Compare to `actor_role`; raise `AuthError` (403) on mismatch.
    - Delegate to lifecycle transition.

### 5. Modify `src/app/modules/ai/router.py`
- 3 routes. Instead of using `require_actor_role(...)` as a hard dependency, add a relaxed dep `require_actor_role(ActorRole.ADMIN, ActorRole.DEV)` that returns the role for the service to re-check against the matrix.

### 6. Create `tests/modules/ai/test_router_tasks_plan.py`
- Matrix branches:
  - Dev-assigned + dev approves plan → `202`.
  - Dev-assigned + admin approves plan → `403`.
  - Agent-assigned + admin approves → `202`.
  - Agent-assigned + dev approves → `403`.
- Reject without feedback → `422`.
- Idempotent replay.
- Illegal state (approve when `status=planning`) → `409`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/models.py` | Modify | `TaskPlan` entity. |
| `src/app/migrations/versions/<ts>_add_task_plans.py` | Create | Migration. |
| `src/app/modules/ai/schemas.py` | Modify | DTOs. |
| `src/app/modules/ai/service.py` | Modify | Service adapters. |
| `src/app/modules/ai/router.py` | Modify | 3 routes. |
| `tests/modules/ai/test_router_tasks_plan.py` | Create | Route tests. |

## Edge Cases & Risks
- **TOCTOU on matrix** — hold `SELECT ... FOR UPDATE` across the matrix compute + transition write. Reassignment mid-approval blocks on the row lock.
- **Agent-submitted plans** — `/plan` by an agent path is actually the orchestrator submitting on behalf of the agent. In v1 that goes through the same HTTP endpoint; role is `admin` since admin mediates. Document in the API spec.

## Acceptance Verification
- [ ] 3 endpoints + `TaskPlan` table.
- [ ] Matrix enforced on approve/reject.
- [ ] Feedback required on reject.
- [ ] Idempotency + illegal-state handling.
- [ ] `uv run pyright`, `ruff`, route tests green.
