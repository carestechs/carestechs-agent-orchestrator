# Implementation Plan: T-148 — GitHub integration test (respx)

## Task Reference
- **Task ID:** T-148
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** AC-8 — lock in GitHub Checks API contract + exact call counts per review cycle.

## Overview
Focused integration suite exercising one task through the full review cycle twice (PAT path and App path), with `respx` asserting exact request counts and bodies.

## Implementation Steps

### Step 1: Test module scaffold
**File:** `tests/integration/test_feat007_github_integration.py`
**Action:** Create

Parametrize across the two auth strategies:

```python
@pytest.mark.parametrize("strategy", ["pat", "app"])
@pytest.mark.asyncio
async def test_review_cycle(strategy, client, db_session, respx_mock, monkeypatch):
    if strategy == "pat":
        monkeypatch.setenv("GITHUB_PAT", "ghp_faketoken")
    else:
        monkeypatch.setenv("GITHUB_APP_ID", "12345")
        monkeypatch.setenv("GITHUB_PRIVATE_KEY", _FAKE_PEM)
    get_settings.cache_clear()
    ...
```

`_FAKE_PEM` is a static RSA key generated once and stored in the test file as a constant — or loaded from `tests/fixtures/github/fake.pem`. Tests never exchange real signatures with GitHub.

### Step 2: Mock the GitHub endpoints
**File:** `tests/integration/test_feat007_github_integration.py`
**Action:** Modify

For the App path, mock:
- `GET /repos/foo/bar/installation` → `{"id": 999}`.
- `POST /app/installations/999/access_tokens` → `{"token": "ghs_test", "expires_at": "<+1h>"}`.

For both paths, mock:
- `POST /repos/foo/bar/check-runs` → `201 {"id": 42}`.
- `PATCH /repos/foo/bar/check-runs/42` → `200 {}`.

Use named routes so `respx_mock.calls.recorded_calls` is readable.

### Step 3: Approval scenario
**File:** `tests/integration/test_feat007_github_integration.py`
**Action:** Modify

Drive `submit_implementation` + `approve_review`. Assert:
- Exactly 1 POST to `/check-runs` with body `{"name": "orchestrator/impl-review", "head_sha": "<sha>", "status": "in_progress"}`.
- Exactly 1 PATCH to `/check-runs/42` with body `{"status": "completed", "conclusion": "success"}`.
- For PAT path: `Authorization: Bearer ghp_faketoken` on both calls.
- For App path: `Authorization: token ghs_test` on both check-run calls; the two App-auth endpoints fire exactly once each.

### Step 4: Rejection scenario
**File:** `tests/integration/test_feat007_github_integration.py`
**Action:** Modify

Same shape; `approve_review` → `reject_review`; PATCH conclusion `failure`.

### Step 5: Token-cache scenario (App path only)
**File:** `tests/integration/test_feat007_github_integration.py`
**Action:** Modify

Drive two back-to-back review cycles on different tasks in the same repo; assert the `access_tokens` endpoint is hit exactly once (cache hit on the second cycle).

### Step 6: No-PR-URL scenario
**File:** `tests/integration/test_feat007_github_integration.py`
**Action:** Modify

`submit_implementation` without `prUrl` → zero calls to `api.github.com` across both cycles.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_feat007_github_integration.py` | Create | Parametrized PAT + App suite. |
| `tests/fixtures/github/fake.pem` | Create | Test RSA key (2048-bit, generated with `openssl genrsa`). |

## Edge Cases & Risks
- **Fake PEM in repo.** The key is static and marked clearly as test-only with a header comment. Do not reuse across test modules via fragile import.
- **Token-cache cross-test leakage.** `AppAuthStrategy` caches per-process; reset by constructing a fresh strategy per test via factory (already does this since `Settings` is reloaded).
- **Respx strict mode.** Use `assert_all_called=True` on the expected routes; other routes must be unroutable so an accidental call surfaces immediately.
- **Header assertions.** `Accept: application/vnd.github+json` and `X-GitHub-Api-Version: 2022-11-28` should be asserted on every call — drift in the client silently breaks API contract.

## Acceptance Verification
- [ ] PAT path: 1 POST + 1 PATCH per cycle, exact body + headers.
- [ ] App path: 1 POST + 1 PATCH per cycle, plus exactly 1 installation-token fetch (cached for the second cycle).
- [ ] Noop path: 0 calls.
- [ ] Rejection test flips conclusion correctly.
- [ ] `uv run pytest tests/integration/test_feat007_github_integration.py` green.
