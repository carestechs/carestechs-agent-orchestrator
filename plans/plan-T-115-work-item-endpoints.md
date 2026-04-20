# Implementation Plan: T-115 — Work-item lifecycle endpoints (S1-S4)

## Task Reference
- **Task ID:** T-115
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-112, T-114

## Overview
Four admin-only endpoints: `POST /api/v1/work-items`, `/{id}/lock`, `/{id}/unlock`, `/{id}/close`. Each runs the idempotency helper, then the state-machine service, and returns the uniform `202 Accepted` envelope.

## Steps

### 1. Modify `src/app/modules/ai/schemas.py`
- Request/response DTOs per `api-spec.md` §FEAT-006:
  - `WorkItemCreateRequest` (externalRef, type, title, sourcePath).
  - `WorkItemLockRequest` (reason: str | None).
  - `WorkItemUnlockRequest` (`{}`).
  - `WorkItemCloseRequest` (notes: str | None).
  - Shared `LifecycleSignalResponse(BaseModel)` with `id, workItemId, taskId, transitionedTo, at`; `meta: LifecycleSignalMeta | None` where `LifecycleSignalMeta(alreadyReceived: bool)`.

### 2. Modify `src/app/modules/ai/service.py`
- Add thin adapters over T-112:
  - `async def open_work_item(session, req, *, opened_by) -> WorkItem`.
  - `async def lock_work_item(session, id, req, *, actor) -> WorkItem`.
  - `async def unlock_work_item(session, id, *, actor) -> WorkItem`.
  - `async def close_work_item(session, id, req, *, actor) -> WorkItem`.
- Each wraps `compute_signal_key` + `check_and_record` before delegating to the lifecycle module; returns `(entity, already_received: bool)`.
- Placeholder: `async def dispatch_task_generation(work_item) -> None` — logs `"task-generation dispatched for work_item %s"` for v1. Seam for follow-up FEAT.

### 3. Modify `src/app/modules/ai/router.py`
- Add 4 routes under `/api/v1/work-items`. Each:
  - Depends on `require_actor_role(ActorRole.ADMIN)`, `get_db_session`, `get_api_key`.
  - Returns `202` with `LifecycleSignalResponse`.
  - Catches `ConflictError` → `409` via global handler (already wired); no local handling.

### 4. Create `tests/modules/ai/test_router_work_items.py`
- For each endpoint:
  - Happy path (right role + valid state).
  - Wrong role → `403`.
  - Illegal state (e.g., lock from `open`) → `409`.
  - Idempotent replay → `202` with `meta.alreadyReceived=true`, no duplicate side effects (no duplicate `task_generation_dispatched` log entry).
- Use `httpx.AsyncClient` + the existing FastAPI test app factory.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/schemas.py` | Modify | Request/response DTOs. |
| `src/app/modules/ai/service.py` | Modify | Service adapters + `dispatch_task_generation` stub. |
| `src/app/modules/ai/router.py` | Modify | 4 new routes. |
| `tests/modules/ai/test_router_work_items.py` | Create | Route tests. |

## Edge Cases & Risks
- **Idempotent dispatch_task_generation** — the stub logs; the idempotency layer ensures it runs once. When the real implementation lands in a follow-up FEAT, preserve the idempotency contract.
- **`WorkItemCreateRequest.externalRef` collision** — UNIQUE constraint returns `IntegrityError`; convert to `ConflictError(409, "work-item-exists")` at the service layer.
- **`reason` / `notes` payloads vary between calls** — idempotency hashes include them, so a caller changing text on retry produces a different key. Document in the API spec or accept this as the idempotency contract.

## Acceptance Verification
- [ ] 4 endpoints live under `/api/v1/work-items/...`.
- [ ] Admin-only role enforced; wrong role → `403`.
- [ ] Idempotent replay returns `alreadyReceived=true`.
- [ ] Illegal transitions → `409`.
- [ ] `dispatch_task_generation` called exactly once on first S1.
- [ ] `uv run pyright`, `ruff`, route tests green.
