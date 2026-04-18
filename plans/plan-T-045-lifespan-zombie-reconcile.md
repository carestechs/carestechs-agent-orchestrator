# Implementation Plan: T-045 — App lifespan + zombie-run reconciliation

## Task Reference
- **Task ID:** T-045
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-037, T-040

## Overview
Convert `main.py` to use FastAPI's `lifespan` context so the supervisor has a clear lifetime. On startup: flip orphan `running` rows to `failed` (previous process died). On shutdown: drain supervised runs gracefully.

## Steps

### 1. Modify `src/app/main.py`
- Remove module-level `app = create_app()` pattern.
- Define `@asynccontextmanager async def lifespan(app: FastAPI)`:
  - **Startup**:
    1. `configure_logging(get_settings().log_level)`.
    2. Instantiate `supervisor = RunSupervisor()` and store on `app.state.supervisor`.
    3. Reconcile zombies: open a short-lived session, `UPDATE runs SET status='failed', stop_reason='error', ended_at=now(), final_state = jsonb_set(coalesce(final_state, '{}'::jsonb), '{zombie_reason}', '"process restart"') WHERE status='running'` (use SQLAlchemy `update(Run)` + `execute`). Log count at INFO.
    4. For each zombie, append a final trace line via `TraceStore.record_step` (trace store injected via factory).
  - **Yield** control to the app.
  - **Shutdown**:
    1. `await supervisor.shutdown(grace=5.0)`.
    2. Log count of runs cancelled on shutdown.
- Update `create_app()` to pass `lifespan=lifespan` to `FastAPI(...)`.
- Update `get_supervisor` dep in `core/dependencies.py` to read from `request.app.state.supervisor` (fallback to the module-level singleton for tests that skip lifespan).

### 2. Modify `src/app/core/dependencies.py`
- `get_supervisor(request: Request)` returns `request.app.state.supervisor` if present; else the module singleton (lazy-created once for test convenience).

### 3. Create `tests/integration/test_lifespan_zombie_reconciliation.py`
- Fixture: insert a `Run` row with `status=running` BEFORE app startup.
- Start app via `httpx.AsyncClient(transport=ASGITransport(app=create_app()))` (triggers lifespan).
- Assert the row transitioned to `status=failed`, `stop_reason=error`, `final_state` contains `zombie_reason`.
- Trace file exists with a line containing `"kind":"step","status":"failed"` (or equivalent).
- Shutdown test: start app, spawn a supervised task, shutdown → assert task cancelled and row transitioned to `cancelled` (distinct from zombie case).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/main.py` | Modify | Lifespan context + zombie sweep. |
| `src/app/core/dependencies.py` | Modify | `get_supervisor` from `app.state`. |
| `tests/integration/test_lifespan_zombie_reconciliation.py` | Create | Startup sweep + graceful shutdown. |
| `tests/conftest.py` | Modify | Ensure `app` fixture uses `create_app()` with lifespan triggered. |

## Edge Cases & Risks
- Race if two processes start simultaneously (double-reload): both try the zombie UPDATE. Idempotent in outcome (row is `failed` either way), but one UPDATE wins. Acceptable; flagged in FEAT-002 §Risks-4.
- Trace file write on a read-only volume: catch + log, don't block startup.
- Tests that don't go through lifespan (some existing unit tests) must still work — that's why `get_supervisor` falls back to a module singleton.

## Acceptance Verification
- [ ] Orphan `running` rows → `failed` at startup.
- [ ] Final trace line written.
- [ ] `shutdown(grace=5s)` drains tasks within that bound.
- [ ] Supervisor identity stable across requests within one process.
