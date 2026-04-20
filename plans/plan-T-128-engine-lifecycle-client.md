# Implementation Plan: T-128 — Engine HTTP client for lifecycle/items

## Task Reference
- **Task ID:** T-128
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** None

## Overview
Thin `httpx.AsyncClient` wrapper around the four flow-engine endpoints FEAT-006 needs. JWT bearer auth cached across calls with transparent re-auth on 401. Bounded retry on transient failures. All calls typed at the boundary.

## Steps

### 1. Modify `src/app/config.py`
- Add fields:
  ```python
  flow_engine_base_url: AnyHttpUrl | None = None
  flow_engine_tenant_api_key: SecretStr | None = None
  ```
- Leave nullable; bootstrap fails loudly if missing + lifecycle is enabled.

### 2. Create `src/app/modules/ai/lifecycle/engine_client.py`
- `class FlowEngineLifecycleClient:`
  - `__init__(base_url, api_key, timeout=10, max_retries=3)`
  - `_ensure_token()` — calls `POST /api/auth/token`, caches `access_token` + `exp`; refreshes when <30 s to expiry or on 401.
  - `async def create_workflow(name, statuses, transitions) -> uuid.UUID`
  - `async def get_workflow_by_name(name) -> uuid.UUID | None` — via `GET /api/workflows?name=...`, used for 409 recovery.
  - `async def create_item(workflow_id, title, external_ref, metadata) -> uuid.UUID`
  - `async def transition_item(item_id, to_status, correlation_id: uuid.UUID, actor: str | None = None) -> dict`
  - `async def ensure_webhook(url, workflow_id | None, event_type) -> uuid.UUID` — idempotent; looks up existing subscription by url+eventType+workflowId and creates if missing.
- All methods use `_request(method, path, **kwargs)` internal that applies retries (500 ms → 4 s backoff, jitter) on `5xx` / `ConnectError` / `ReadTimeout`.
- On 4xx, raise `EngineError(code="lifecycle-engine", http_status=..., detail=...)`.

### 3. Create `tests/modules/ai/lifecycle/test_engine_client.py`
- Fixture builds the client with `base_url="http://engine.test"`.
- `respx` routes for `/api/auth/token`, the four endpoints.
- Tests:
  - Happy path for each method.
  - 401 on a mid-session call → re-auth, retry, succeed.
  - 502 → retry twice, then succeed on attempt 3.
  - 502 on every attempt → `EngineError` raised.
  - 422 body surfaces in `EngineError.detail`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/config.py` | Modify | Two new fields. |
| `src/app/modules/ai/lifecycle/engine_client.py` | Create | Client. |
| `tests/modules/ai/lifecycle/test_engine_client.py` | Create | `respx`-driven tests. |
| `tests/test_config.py` | Modify | Extend field set. |

## Edge Cases & Risks
- **Token refresh race.** Two concurrent requests both see an expired token; add `asyncio.Lock` around `_ensure_token` to serialize refreshes. Only one POST to `/api/auth/token` fires.
- **Backoff + jitter.** Use `asyncio.sleep(base * 2**attempt + random())`; cap at 5 s.
- **Engine URL missing.** If `flow_engine_base_url is None`, the factory returns a clear-error `NoopEngineClient` that raises on any call — prevents silent failures in prod.

## Acceptance Verification
- [ ] Four typed methods live on the client.
- [ ] JWT caching + transparent re-auth on 401.
- [ ] Retry on 5xx with bounded backoff.
- [ ] `EngineError` on 4xx with body preserved.
- [ ] `uv run pyright`, `ruff`, `pytest tests/modules/ai/lifecycle/test_engine_client.py` green.
