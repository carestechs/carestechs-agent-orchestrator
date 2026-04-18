# Implementation Plan: T-012 — FastAPI app factory + router registration + handler wiring

## Task Reference
- **Task ID:** T-012
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** Binds all foundational pieces into a runnable service. Addresses AC-2.

## Overview
Implement `create_app()` in `app/main.py`: configure logging, register exception handlers, install `RawBodyMiddleware`, module-level `app = create_app()` for uvicorn. Router registration is deferred to T-014/T-015/T-016 which add their routers to the app — but we include the `app.include_router` calls with empty stubs so the structure is clear.

## Implementation Steps

### Step 1: Implement `create_app()` factory
**File:** `src/app/main.py`
- Call `configure_logging(get_settings().log_level)` — but guard with try/except for when settings aren't available (CLI --help, tests).
- Create `FastAPI(title=...)`.
- Call `register_exception_handlers(app)`.
- Add `RawBodyMiddleware` for `/hooks/` prefix.
- Module-level `app = create_app()`.

### Step 2: Test app boot
**File:** `tests/test_app_boot.py`
- Test that `create_app()` returns a FastAPI instance.
- Test that `/openapi.json` is accessible.
- Test that unhandled exceptions return 500 Problem Details.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/main.py` | Modify | `create_app()` factory |
| `tests/test_app_boot.py` | Create | Boot + openapi tests |
