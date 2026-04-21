# Implementation Plan: T-143 — `HttpxGitHubChecksClient` + `NoopGitHubChecksClient`

## Task Reference
- **Task ID:** T-143
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** AC-1 (three impls land) + AC-5 (Noop keeps FEAT-006 functional without credentials).

## Overview
Add the two concrete `GitHubChecksClient` implementations on top of T-142's protocol + auth strategies.

## Implementation Steps

### Step 1: `HttpxGitHubChecksClient`
**File:** `src/app/modules/ai/github/checks.py`
**Action:** Modify

Constructor takes `auth: AuthStrategy` and `http: httpx.AsyncClient` (injected, not owned — follow `lifecycle/engine_client.py`).

`create_check`:
- `POST https://api.github.com/repos/{owner}/{repo}/check-runs`
- Body: `{"name": name, "head_sha": head_sha, "status": "in_progress"}`.
- Headers from `auth.headers_for(owner, repo)` + `X-GitHub-Api-Version: 2022-11-28`.
- On 201 → return `str(resp.json()["id"])`.
- On 4xx (≠ 401) → raise `ProviderError(code="github-check-create-failed", http_status=..., body=...)`.
- On 401 → raise `AuthError(code="github-auth-failed")`.
- On 5xx / 429 / `httpx.TimeoutException` / `httpx.ConnectError` → raise `ProviderError(code="github-transient", http_status=...)`.

`update_check`:
- `PATCH https://api.github.com/repos/{owner}/{repo}/check-runs/{check_id}`.
- Body: `{"status": "completed", "conclusion": conclusion}`.
- Same error mapping as `create_check`.

Do **not** auto-retry here — the service layer (T-145) decides whether a failed update is fatal. Every call logs at INFO with `owner`, `repo`, `check_id` (on update) using the structured logger per CLAUDE.md.

### Step 2: `NoopGitHubChecksClient`
**File:** `src/app/modules/ai/github/checks.py`
**Action:** Modify

Module-level `_noop_warned = False` guard.

```python
class NoopGitHubChecksClient:
    async def create_check(self, *, owner, repo, head_sha, name=CHECK_NAME) -> str:
        global _noop_warned
        if not _noop_warned:
            logger.warning("GitHub merge-gating disabled — no credentials configured; check-runs are no-ops")
            _noop_warned = True
        return "noop"
    async def update_check(self, *, owner, repo, check_id, conclusion) -> None:
        return None
```

### Step 3: Unit tests — clients
**File:** `tests/modules/ai/github/test_checks.py`
**Action:** Create

Use `respx` against `api.github.com`.

Cases:
- `create_check` happy path: body matches, returns stringified id.
- `update_check` happy path for `success` and `failure`.
- Auth header propagated from `PatAuthStrategy`.
- 500 → `ProviderError` with `http_status=500` + body.
- 401 → `AuthError`.
- 404 → `ProviderError` (not retried here).
- Timeout → `ProviderError(code="github-transient")`.
- Noop `create_check` returns `"noop"`, logs exactly once across multiple calls.
- Noop `update_check` is a no-op.

Use `caplog` + `capsys` carefully; reset `_noop_warned` between tests via fixture.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/github/checks.py` | Modify | Add Httpx + Noop clients. |
| `src/app/core/exceptions.py` | Modify | Add `github-check-create-failed`, `github-auth-failed`, `github-transient` codes if not already present. |
| `tests/modules/ai/github/test_checks.py` | Create | Client unit tests. |

## Edge Cases & Risks
- **Rate-limit response bodies** contain `X-RateLimit-Reset`; not surfaced in v1. Document for future ops work.
- **Check-run `external_id`.** The GitHub API accepts an idempotency-ish `external_id`. T-145 may want to pass `task_id` here to make re-POSTs safe. Plan T-145 will extend `create_check`'s signature if needed — leave room in the protocol by allowing `**kwargs` forwarding, but don't widen the contract yet.
- **Noop log flag is module-level.** A test-wide fixture must reset it; otherwise ordering across tests produces false negatives.

## Acceptance Verification
- [ ] Httpx client POST/PATCH bodies + headers asserted by `respx`.
- [ ] Error mapping covers 4xx (non-auth), 401, 5xx, timeout.
- [ ] Noop: one log, `"noop"` id, no-op update.
- [ ] `uv run pyright`, `ruff`, `pytest tests/modules/ai/github/` green.
