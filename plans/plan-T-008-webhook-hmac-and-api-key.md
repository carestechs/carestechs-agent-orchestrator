# Implementation Plan: T-008 — HMAC webhook verifier + API-key Bearer dependency

## Task Reference
- **Task ID:** T-008
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** Load-bearing for the entire webhook trust boundary (AC-6). Control-plane auth pattern for the rest of the API.

## Overview
Implement HMAC-SHA256 signature verification for the inbound engine webhooks with raw-body preservation, and Bearer-token API-key auth for the control plane. Designed so the webhook route can both reject unsigned events *and* persist them (per data model), which requires verifying without raising.

## Implementation Steps

### Step 1: Raw-body middleware
**File:** `src/app/core/middleware.py`
**Action:** Modify

Starlette reads the request body once; route handlers and dependencies sharing a body require caching. Add an ASGI middleware that reads and stashes the raw body on `request.state.raw_body` for routes under a configurable prefix (`/hooks/`). Outside the prefix, no-op.

Implementation sketch (ASGI-level to avoid double-read):

```python
class RawBodyMiddleware:
    def __init__(self, app: ASGIApp, *, prefix: str = "/hooks/") -> None:
        self.app, self.prefix = app, prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(self.prefix):
            return await self.app(scope, receive, send)
        body = b""
        more = True
        while more:
            msg = await receive()
            body += msg.get("body", b"")
            more = msg.get("more_body", False)
        scope["state"] = scope.get("state", {})
        scope["state"]["raw_body"] = body
        async def replay():
            return {"type": "http.request", "body": body, "more_body": False}
        await self.app(scope, replay, send)
```

Registered in `create_app()` (T-012) via `app.add_middleware(RawBodyMiddleware)`.

### Step 2: HMAC verifier helpers
**File:** `src/app/core/webhook_auth.py`
**Action:** Modify

```python
def sign_body(body: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"

def verify_signature(body: bytes, header: str | None, secret: str) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = sign_body(body, secret)
    return hmac.compare_digest(header, expected)
```

`sign_body` is exported for test use (conftest builds valid payloads). `verify_signature` returns a bool — never raises — so the route can persist the event either way.

### Step 3: Webhook signature dependency
**File:** `src/app/core/webhook_auth.py`
**Action:** Modify

```python
async def require_engine_signature(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> bool:
    body: bytes = request.state.raw_body       # stashed by middleware
    header = request.headers.get("x-engine-signature")
    ok = verify_signature(body, header, settings.engine_webhook_secret.get_secret_value())
    request.state.signature_ok = ok
    return ok
```

Returns the bool. Crucially does not raise — the route handler decides how to respond (401 + persist for `False`; continue for `True`).

### Step 4: API-key Bearer dependency
**File:** `src/app/core/api_auth.py`
**Action:** Modify

```python
async def require_api_key(
    authorization: Annotated[str | None, Header()],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    expected = settings.orchestrator_api_key.get_secret_value()
    if not hmac.compare_digest(token, expected):
        raise AuthError("invalid api key")
```

Raises `AuthError` on failure — global handler converts to 401 Problem Details (T-005).

### Step 5: Tests
**File:** `tests/core/test_webhook_auth.py`
**Action:** Create

- `sign_body` + `verify_signature` round-trip passes.
- Missing header → `False`.
- Wrong prefix (`"md5=..."`) → `False`.
- Wrong digest → `False`.
- Constant-time compare sanity: pass two same-length but different strings and same-length equal strings, assert consistent returns (doesn't prove timing, just correctness).

**File:** `tests/core/test_api_auth.py`
**Action:** Create

- Missing `Authorization` header → `AuthError` raised.
- Malformed (`"Token foo"`) → `AuthError`.
- Wrong token → `AuthError`.
- Correct token → no exception.
- Integration: attach `require_api_key` to a throwaway FastAPI route, use `AsyncClient` without and with the header, assert 401 vs 200.

## Files Affected

| File | Action | Summary |
|------|--------|---------|
| `src/app/core/middleware.py` | Modify | `RawBodyMiddleware` ASGI middleware |
| `src/app/core/webhook_auth.py` | Modify | `sign_body`, `verify_signature`, `require_engine_signature` |
| `src/app/core/api_auth.py` | Modify | `require_api_key` Bearer dep |
| `tests/core/test_webhook_auth.py` | Create | Round-trip + failure paths |
| `tests/core/test_api_auth.py` | Create | Bearer happy path + failures |

## Edge Cases & Risks

- **Raw-body double-read.** If any downstream handler calls `await request.body()` again on Starlette, the middleware's replay-receive must provide a `more_body=False` message. The sketch above does this. Verify with an integration test that POSTs JSON and both parses it and reads `request.state.raw_body`.
- **Scope for middleware.** Applying the middleware globally (no prefix) forces every request to buffer its body in memory. For v1 we only need it on `/hooks/`, so the prefix gate matters. Revisit when more signed endpoints exist.
- **`hmac.compare_digest` length mismatch.** Silently returns `False` for different-length inputs — safe. Don't short-circuit on length checks yourself.
- **Secret rotation.** v1 supports only one secret. When rotation arrives (post-v1), extend `verify_signature` to accept a list of secrets and succeed if any matches; flag at that time.
- **Case-sensitive headers.** Starlette normalizes headers to lowercase on ingress; use `x-engine-signature`. Tests should send `X-Engine-Signature` to prove both cases work.
- **`require_engine_signature` leaving state behind between tests.** `request.state` is per-request, so no cross-test bleed — but the middleware injects `state.raw_body` into the ASGI scope which FastAPI copies into `request.state`. Verify with a per-test teardown that inspects state isn't global.

## Acceptance Verification

- [ ] **`sign_body` / `verify_signature` round-trip:** green.
- [ ] **Malformed header cases:** all return `False` without raising.
- [ ] **`require_engine_signature` does not raise:** even on failed verification; sets `request.state.signature_ok = False`.
- [ ] **`require_api_key` happy + failure paths:** green.
- [ ] **Raw-body middleware preserves body:** integration test POSTs JSON, reads `raw_body`, and also parses via Pydantic — both succeed.
