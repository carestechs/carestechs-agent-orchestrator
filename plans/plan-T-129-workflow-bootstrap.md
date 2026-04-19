# Implementation Plan: T-129 — Workflow bootstrap on startup

## Task Reference
- **Task ID:** T-129
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-128

## Overview
At orchestrator startup, ensure the two FEAT-006 workflows exist in the engine. Cache their IDs locally in `engine_workflows` so every subsequent `create_item` call knows where to point.

## Steps

### 1. Create `src/app/modules/ai/lifecycle/declarations.py`
- Module-level constants describing the two workflows:
  ```python
  WORK_ITEM_STATUSES = ["open", "in_progress", "locked", "ready", "closed"]
  WORK_ITEM_TRANSITIONS = [
      ("open", "in_progress", "approve-first-task"),
      ("in_progress", "locked", "lock"),
      ("locked", "in_progress", "unlock"),
      ("in_progress", "ready", "all-tasks-terminal"),
      ("ready", "closed", "close"),
  ]
  TASK_STATUSES = ["proposed", "approved", "assigning", "planning", "plan_review",
                   "implementing", "impl_review", "done", "deferred"]
  TASK_TRANSITIONS = [
      ("proposed", "approved", "approve"),
      ("approved", "assigning", "t4-derived"),
      ("assigning", "planning", "assign"),
      ("planning", "plan_review", "submit-plan"),
      ("plan_review", "implementing", "approve-plan"),
      ("plan_review", "planning", "reject-plan"),
      ("implementing", "impl_review", "submit-impl"),
      ("impl_review", "done", "approve-review"),
      ("impl_review", "implementing", "reject-review"),
      # Deferral edges: any non-terminal -> deferred
  ] + [(s, "deferred", "defer") for s in TASK_STATUSES if s not in {"done", "deferred"}]
  ```

### 2. Modify `src/app/modules/ai/models.py`
- Add:
  ```python
  class EngineWorkflow(Base):
      __tablename__ = "engine_workflows"
      name: Mapped[str] = mapped_column(primary_key=True)
      engine_workflow_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
      created_at: Mapped[datetime] = mapped_column(server_default=func.now())
  ```

### 3. Alembic migration — `add_engine_workflows`.

### 4. Create `src/app/modules/ai/lifecycle/bootstrap.py`
- `async def ensure_workflows(session, client) -> dict[str, uuid.UUID]`:
  - For each workflow name (`work_item_workflow`, `task_workflow`):
    - Look up local `engine_workflows` row; if present, use cached id.
    - Else call `client.create_workflow(...)`. On 409, fall back to `client.get_workflow_by_name(name)` and upsert locally.
  - Returns `{name: engine_id}`.

### 5. Modify `src/app/lifespan.py`
- In the startup block, after DB init: build the `FlowEngineLifecycleClient`, call `ensure_workflows(...)`, stash the result on `app.state.engine_workflow_ids` for dep injection.
- If `flow_engine_base_url is None`, log a warning and skip bootstrap (lifecycle endpoints will error on first use).

### 6. Create `tests/modules/ai/lifecycle/test_bootstrap.py`
- Happy path: cold start, both workflows created.
- Restart: local cache hit, no engine calls.
- 409 recovery: create returns 409, fallback to GET, id cached.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/lifecycle/declarations.py` | Create | Workflow state + transition lists. |
| `src/app/modules/ai/lifecycle/bootstrap.py` | Create | `ensure_workflows`. |
| `src/app/modules/ai/models.py` | Modify | `EngineWorkflow`. |
| Alembic migration | Create | `engine_workflows` table. |
| `src/app/lifespan.py` | Modify | Startup hook. |
| `tests/modules/ai/lifecycle/test_bootstrap.py` | Create | Tests. |

## Edge Cases & Risks
- **Engine not configured.** Log + skip. Don't crash startup — that breaks dev workflows for contributors not running the engine.
- **Workflow schema drift.** If someone changes `TASK_STATUSES` after a workflow is already registered, the engine's stored definition is stale. v1: warn, don't auto-migrate. Follow-up: "update workflow schema" task.

## Acceptance Verification
- [ ] Two workflows registered at startup.
- [ ] Idempotent across restarts.
- [ ] 409 recovery path works.
- [ ] Startup does not crash when engine is unreachable.
- [ ] `uv run pyright`, `ruff`, tests green.
