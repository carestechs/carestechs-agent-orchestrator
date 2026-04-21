# Implementation Plan: T-144 — Composition-root factory + FastAPI dependency

## Task Reference
- **Task ID:** T-144
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** AC-2 — deterministic `App > PAT > Noop` priority; single injection point for service layer.

## Overview
`get_github_checks_client(settings)` returns the right implementation, and a FastAPI dependency wraps it so routes can inject `GitHubChecksClient` just like `FlowEngineLifecycleClient`.

## Implementation Steps

### Step 1: Factory
**File:** `src/app/core/github.py`
**Action:** Create

```python
from __future__ import annotations
import httpx
from app.config import Settings
from app.modules.ai.github.auth import AppAuthStrategy, PatAuthStrategy
from app.modules.ai.github.checks import (
    GitHubChecksClient, HttpxGitHubChecksClient, NoopGitHubChecksClient,
)

_shared_http: httpx.AsyncClient | None = None

def _http() -> httpx.AsyncClient:
    global _shared_http
    if _shared_http is None:
        _shared_http = httpx.AsyncClient(timeout=30.0)
    return _shared_http

def get_github_checks_client(settings: Settings) -> GitHubChecksClient:
    if settings.github_app_id and settings.github_private_key:
        auth = AppAuthStrategy(
            app_id=settings.github_app_id,
            private_key=settings.github_private_key.get_secret_value(),
            http=_http(),
        )
        return HttpxGitHubChecksClient(auth=auth, http=_http())
    if settings.github_pat:
        auth = PatAuthStrategy(settings.github_pat.get_secret_value())
        return HttpxGitHubChecksClient(auth=auth, http=_http())
    return NoopGitHubChecksClient()
```

Add a teardown hook `async def close_shared_http()` for the lifespan to call on shutdown. Wire into `src/app/lifespan.py` alongside the existing engine-client shutdown.

### Step 2: Migrate `doctor`'s helper
**File:** `src/app/cli.py`
**Action:** Modify

Replace T-140's inline `_resolved_github_strategy` with a lookup that inspects the factory's return type: `isinstance(client, NoopGitHubChecksClient) → "noop"`, else check the `auth` attribute's class. Keeps the priority logic single-sourced.

### Step 3: FastAPI dependency
**File:** `src/app/modules/ai/dependencies.py`
**Action:** Modify

Add next to `get_lifecycle_engine_client`:

```python
async def get_github_checks_client_dep(
    settings: Annotated[Settings, Depends(get_settings)],
) -> GitHubChecksClient:
    return get_github_checks_client(settings)
```

### Step 4: Factory tests
**File:** `tests/core/test_github_factory.py`
**Action:** Create

Cases (parametrized):
- No credentials → `NoopGitHubChecksClient`.
- PAT only → `HttpxGitHubChecksClient` with `PatAuthStrategy`.
- App id + private key → `HttpxGitHubChecksClient` with `AppAuthStrategy`.
- App + PAT both set is already rejected by T-140's validator; add a sanity test that `Settings()` raises before reaching the factory.
- Half-set App (id only, or key only) → `Settings` raises (T-140 validator).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/core/github.py` | Create | Factory + shared `httpx.AsyncClient`. |
| `src/app/lifespan.py` | Modify | Close shared client on shutdown. |
| `src/app/cli.py` | Modify | `doctor` uses the factory. |
| `src/app/modules/ai/dependencies.py` | Modify | FastAPI dep. |
| `tests/core/test_github_factory.py` | Create | Priority tests. |

## Edge Cases & Risks
- **Shared `AsyncClient` lifetime.** Must be closed on app shutdown; not closing leaks connections. Lifespan is the right place.
- **Settings-cache interaction.** `get_settings` is `@lru_cache`'d; tests must `get_settings.cache_clear()` in teardown (already done in the conftest env fixture).

## Acceptance Verification
- [ ] Factory returns correct class for each of the three branches.
- [ ] Half-set / both-set configs rejected at `Settings()` construction.
- [ ] `get_github_checks_client_dep` importable and injectable.
- [ ] `uv run pyright`, `ruff`, `pytest tests/core/test_github_factory.py` green.
