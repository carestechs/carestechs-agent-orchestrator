# Implementation Plan: T-113 — Task state-machine service + T4 + approval matrix

## Task Reference
- **Task ID:** T-113
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** L
- **Dependencies:** T-108, T-109, T-110

## Overview
All task transitions + the approval-matrix helper that tells callers which `ActorRole` is required to approve at each stage. T4 fires inside `approve_task`.

## Steps

### 1. Modify `src/app/config.py`
- Add field: `solo_dev_mode: bool = Field(default=True, alias="SOLO_DEV_MODE")`.

### 2. Create `src/app/modules/ai/lifecycle/tasks.py`
- Transition functions:
  - `propose_task(session, *, work_item_id, external_ref, title, proposer_type, proposer_id) -> Task` — T1.
  - `approve_task(session, task_id, *, actor) -> Task` — T2+T4. Writes `Approval(stage=proposed, decision=approve)`; advances to `approved`; immediately advances to `assigning` (T4); then calls `work_items.maybe_advance_to_in_progress(...)` on the parent.
  - `reject_task_proposal(session, task_id, *, actor, feedback) -> Task` — T3. Requires non-empty feedback (raise `ValidationError` else). Writes `Approval(stage=proposed, decision=reject, feedback=...)`; keeps status `proposed`.
  - `assign_task(session, task_id, *, assignee_type, assignee_id, assigned_by) -> TaskAssignment` — T5. Supersedes prior active assignment and inserts new row in same tx. Advances `assigning → planning`. Only valid from `assigning`.
  - `submit_plan(session, task_id, *, plan_path, plan_sha, submitted_by) -> Task` — T6. Advances `planning → plan_review`. (Persistence of the plan row itself lives in T-117.)
  - `approve_plan(session, task_id, *, actor, actor_role) -> Task` — T7. Validates `actor_role` matches `approval_matrix(...)`. Writes `Approval(stage=plan, decision=approve)`; advances to `implementing`.
  - `reject_plan(session, task_id, *, actor, actor_role, feedback) -> Task` — T8. Approval matrix check + non-empty feedback. Advances back to `planning`.
  - `submit_implementation(session, task_id, *, submitted_by) -> Task` — T9. `implementing → impl_review`. (Persistence lives in T-118.)
  - `approve_review(session, task_id, *, actor, actor_role) -> Task` — T10. Matrix check. Advances to `done`. Caller fires `maybe_advance_to_ready`.
  - `reject_review(session, task_id, *, actor, actor_role, feedback) -> Task` — T11. Matrix check + feedback. Advances back to `implementing`.
  - `defer_task(session, task_id, *, actor, reason) -> Task` — T12. Rejects if `status in {done, deferred}`. Sets `deferred_from=<prior>`, `status=deferred`.
- All transitions use `SELECT ... FOR UPDATE` on the task row.

### 3. Create `src/app/modules/ai/lifecycle/approval_matrix.py`
- `def approval_matrix(task: Task, assignment: TaskAssignment | None, stage: ApprovalStage, *, solo_dev: bool) -> ActorRole`
  - `stage=proposed` → always `ADMIN`.
  - `stage=plan` → if `assignment.assignee_type == dev` → `DEV`; else `ADMIN`.
  - `stage=impl` → if `solo_dev` → `ADMIN`; else `DEV`.
- Pure function, no DB access; caller passes loaded `Task` + `TaskAssignment`.

### 4. Create `tests/modules/ai/lifecycle/test_tasks.py`
- One test per transition (happy + illegal-state case).
- Rejection preserves owner: insert 3 `reject_plan` rows; assert count, assert `TaskAssignment` unchanged.
- Defer from each non-terminal state accepted; from `done`/`deferred` → `409`.

### 5. Create `tests/modules/ai/lifecycle/test_approval_matrix.py`
- Parameterized tests for every (stage × assignment × solo_dev) combination.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/config.py` | Modify | `solo_dev_mode` flag. |
| `src/app/modules/ai/lifecycle/tasks.py` | Create | Transition functions. |
| `src/app/modules/ai/lifecycle/approval_matrix.py` | Create | Matrix pure function. |
| `tests/modules/ai/lifecycle/test_tasks.py` | Create | Transition tests. |
| `tests/modules/ai/lifecycle/test_approval_matrix.py` | Create | Matrix tests. |

## Edge Cases & Risks
- **TOCTOU on matrix check** — approver role could change between check and transition if a concurrent reassignment lands. Hold the `SELECT ... FOR UPDATE` on the task row across the matrix call and the transition write.
- **Feedback-required guard** — enforce in service, not the route handler, so CLI + HTTP share the rule.
- **W5 fires in caller, not here** — keep the transition function pure to its task; the caller decides whether to chase derivations.

## Acceptance Verification
- [ ] All 12 transitions implemented and unit-tested.
- [ ] T4 fires inside `approve_task`.
- [ ] `approval_matrix` pure function covers all branches.
- [ ] Rejections require non-empty feedback.
- [ ] Defer rejects from terminal states with `409`.
- [ ] `solo_dev_mode` config flag drives `impl` matrix branch.
- [ ] `uv run pyright`, `ruff`, tests green.
