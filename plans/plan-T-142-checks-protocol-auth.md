# Implementation Plan: T-142 — `GitHubChecksClient` protocol + auth strategies

## Task Reference
- **Task ID:** T-142
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** AC-1 / AC-2 — protocol + auth strategies are the foundation for Httpx and Noop clients.

## Overview
Define the `GitHubChecksClient` Protocol and two `AuthStrategy` implementations: `PatAuthStrategy` (static Bearer) and `AppAuthStrategy` (RS256 JWT → installation token, per-repo cached 50 min, refresh-serialized with `asyncio.Lock`). No concrete `GitHubChecksClient` yet — that's T-143.

## Implementation Steps

### Step 1: Add `PyJWT[crypto]` dependency
**File:** `pyproject.toml`
**Action:** Modify

Add `pyjwt[crypto] >= 2.9` under project dependencies. Follow existing dep block conventions. Run `uv lock && uv sync`.

### Step 2: Define the `GitHubChecksClient` protocol
**File:** `src/app/modules/ai/github/checks.py`
**Action:** Create

```python
from __future__ import annotations
from typing import Literal, Protocol, runtime_checkable

CheckConclusion = Literal["success", "failure"]
CHECK_NAME = "orchestrator/impl-review"

@runtime_checkable
class GitHubChecksClient(Protocol):
    async def create_check(
        self, *, owner: str, repo: str, head_sha: str, name: str = CHECK_NAME
    ) -> str: ...
    async def update_check(
        self, *, owner: str, repo: str, check_id: str, conclusion: CheckConclusion
    ) -> None: ...
```

Centralize `CHECK_NAME` here per the "lock the constant in one place" risk from the task list.

### Step 3: Define `AuthStrategy` + `PatAuthStrategy`
**File:** `src/app/modules/ai/github/auth.py`
**Action:** Create

```python
class AuthStrategy(Protocol):
    async def headers_for(self, *, owner: str, repo: str) -> dict[str, str]: ...

class PatAuthStrategy:
    def __init__(self, pat: str) -> None:
        self._pat = pat
    async def headers_for(self, *, owner: str, repo: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._pat}", "Accept": "application/vnd.github+json"}
```

### Step 4: Implement `AppAuthStrategy`
**File:** `src/app/modules/ai/github/auth.py`
**Action:** Modify

Class members: `app_id`, `private_key` (PEM), `_tokens: dict[str, _CachedToken]`, `_locks: dict[str, asyncio.Lock]`, shared `httpx.AsyncClient`.

`headers_for`:
1. Key = `f"{owner}/{repo}"`.
2. Under that repo's lock: if cached token present AND expires > now + 60 s → return it.
3. Otherwise:
   a. Sign RS256 JWT with claims `{iat: now-60, exp: now+540, iss: app_id}` (≤10 min lifetime; lean under).
   b. `GET /repos/{owner}/{repo}/installation` with the JWT → `installation_id`.
   c. `POST /app/installations/{installation_id}/access_tokens` with the JWT → `{token, expires_at}`.
   d. Cache under the repo key with `expires_at - 10min` (50-min effective TTL).
4. Return `{Authorization: f"token {token}", Accept: "application/vnd.github+json"}`.

Add `@file:/path/to/key.pem` prefix support: if `private_key.startswith("@file:")`, read the file at init; otherwise treat as raw PEM. Strip whitespace.

### Step 5: Unit tests — strategies
**File:** `tests/modules/ai/github/test_auth.py`
**Action:** Create

Use `respx` to mock the two App-auth GitHub endpoints.

Cases:
- `PatAuthStrategy` returns correct header dict.
- `AppAuthStrategy.headers_for` fetches + caches; second call hits cache (one API call total).
- Expired cache triggers refetch.
- Two concurrent `headers_for` calls for the same repo serialize through the lock (one refetch, not two).
- Different repos get independent caches.
- JWT claims: `iss == app_id`, `exp - iat <= 600`.
- `@file:` prefix reads key from disk; raw PEM path works.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `pyproject.toml` | Modify | Add `pyjwt[crypto]`. |
| `src/app/modules/ai/github/checks.py` | Create | Protocol + `CHECK_NAME`. |
| `src/app/modules/ai/github/auth.py` | Create | `AuthStrategy` protocol + `PatAuthStrategy` + `AppAuthStrategy`. |
| `tests/modules/ai/github/test_auth.py` | Create | Strategy unit tests. |

## Edge Cases & Risks
- **Clock skew.** JWT `iat` uses `now-60` to absorb skew; GitHub documents this.
- **Installation lookup caching.** `GET /repos/.../installation` result is also cached with the token to avoid a double round-trip per refresh.
- **Memory growth.** Per-process cache unbounded in repo count. For the scale FEAT-007 targets (handful of repos), acceptable; if it ever balloons, swap to `cachetools.TTLCache`.
- **Concurrent refresh race.** Solved by per-repo `asyncio.Lock`. Test must prove it with `asyncio.gather` of two calls against a single-fire respx mock.

## Acceptance Verification
- [ ] Protocol signatures match the task's contract exactly.
- [ ] `PatAuthStrategy` unit test green.
- [ ] `AppAuthStrategy` cache + lock + `@file:` tests green.
- [ ] `uv run pyright`, `ruff`, `pytest tests/modules/ai/github/` green.
