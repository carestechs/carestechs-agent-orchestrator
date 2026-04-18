# Implementation Plan: T-017 — Flow-engine HTTP client skeleton

## Task Reference
- **Task ID:** T-017
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S

## Overview
Create a typed httpx.AsyncClient wrapper (`FlowEngineClient`) that centralises all HTTP communication with the carestechs-flow-engine. The client reads base URL and auth header from Settings, wraps httpx exceptions in `EngineError`, and exposes `health()` (never-raise bool) and a stub `dispatch_node()`. It is injectable via FastAPI dependency.

## Implementation Steps

### Step 1: Extend EngineError to carry correlation metadata
**File:** `src/app/core/exceptions.py`
**Action:** Modify
Override `__init__` on `EngineError` to accept optional `engine_http_status`, `engine_correlation_id`, and `original_body` fields. These are stored as instance attributes so callers can inspect them.

### Step 2: Create FlowEngineClient
**File:** `src/app/modules/ai/engine_client.py`
**Action:** Create
- Class wrapping `httpx.AsyncClient` with base_url and optional Bearer auth from Settings.
- `health() -> bool`: GET `/health`, return True on 2xx, False on anything else (including connection errors). Never raises.
- `dispatch_node(...)`: raises `NotImplementedYet`.
- Private `_wrap_error` helper that converts httpx exceptions to `EngineError` with correlation metadata.

### Step 3: Register FastAPI dependency
**File:** `src/app/modules/ai/dependencies.py`
**Action:** Modify
Add `get_engine_client` dependency that constructs `FlowEngineClient` from Settings.

### Step 4: Write tests
**File:** `tests/modules/ai/test_engine_client.py`
**Action:** Create
- Test `health()` returns True on 200.
- Test `health()` returns False on 500.
- Test `health()` returns False on connection error.
- Test `dispatch_node()` raises `NotImplementedYet`.
- Test that HTTP errors are wrapped in `EngineError` with metadata.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/core/exceptions.py` | Modify | Extend EngineError with correlation metadata |
| `src/app/modules/ai/engine_client.py` | Create | FlowEngineClient class |
| `src/app/modules/ai/dependencies.py` | Modify | Add get_engine_client dependency |
| `tests/modules/ai/test_engine_client.py` | Create | Tests using respx |

## Edge Cases & Risks
- `health()` must catch all exceptions (including `httpx.ConnectError`, DNS failures, timeouts) and return False.
- `engine_correlation_id` may not be present in engine responses; extract from `x-correlation-id` header if available.
- The client must not leak httpx exceptions past the boundary.

## Acceptance Verification
- [ ] FlowEngineClient is injectable via FastAPI dependency; tests override it with a respx mock.
- [ ] `health()` returns True on 2xx, False on any other response (incl. connection errors), NEVER raises.
- [ ] `dispatch_node` raises NotImplementedYet.
- [ ] httpx exceptions wrapped in EngineError with http_status, engine_correlation_id, and original body.
- [ ] `uv run pytest tests/modules/ai/test_engine_client.py -v` passes.
- [ ] `uv run ruff check .` clean.
- [ ] `uv run ruff format --check .` clean.
- [ ] `uv run pyright` 0 errors.
