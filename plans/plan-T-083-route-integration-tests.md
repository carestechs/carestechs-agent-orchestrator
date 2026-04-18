# Implementation Plan: T-083 — Route integration tests

## Task Reference
- **Task ID:** T-083
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-080

## Overview
Integration tests for `GET /api/v1/runs/{id}/trace` covering happy path, 404, filters, and the noop-backend branch.  Reuses `integration_env` from `tests/integration/env.py`.

## Steps

### 1. Create `tests/integration/test_trace_stream_route.py`
Structure:
- Shared imports + `tests.conftest.API_KEY`.
- Helper `_collect_stream(env, run_id, **params)` that opens `env.client.stream(...)`, collects `[line async for line in resp.aiter_lines() if line]`, returns the list.
- Helper `_completed_run(env)` that starts a short stub-policy run via `POST /api/v1/runs`, polls until terminal, returns the run id.

Test cases:

1. **`test_completed_run_stream_yields_every_record`**
   - Run a 3-step stub-policy run to completion.
   - `async with env.client.stream("GET", f"/api/v1/runs/{run_id}/trace", headers=auth, timeout=5.0) as resp:`
   - Assert `resp.status_code == 200` and `resp.headers["content-type"] == "application/x-ndjson"`.
   - Collect lines; parse each as JSON; assert every record has `"kind"` and `"data"`.
   - Assert every kind from the writer appears at least once: `"step"`, `"policy_call"`, `"webhook_event"`.

2. **`test_unknown_run_returns_404_problem_details`**
   - `uuid.uuid4()` (not seeded).
   - `resp = await env.client.get(f"/api/v1/runs/{random}/trace", headers=auth)`.
   - `assert resp.status_code == 404`.
   - `assert resp.headers["content-type"].startswith("application/problem+json")`.
   - Parse body, assert `type` URI ends with `not-found`.

3. **`test_kind_filter_narrows_to_steps_only`**
   - Completed run as in #1.
   - `_collect_stream(env, run_id, kind="step")`.
   - Parse each line; assert every record's `kind == "step"`.

4. **`test_kind_filter_accepts_multiple`**
   - Pass `params={"kind": ["step", "policy_call"]}` via httpx.
   - Assert every yielded `kind` is in `{"step", "policy_call"}`.

5. **`test_since_filter_excludes_earlier_records`**
   - Completed run.
   - `since = datetime.now(UTC) + timedelta(hours=1)` (future).
   - Stream yields nothing; iterator closes.

6. **`test_noop_backend_returns_empty_stream`**
   - Override `get_trace_store` to return `NoopTraceStore()`.
   - `resp = await env.client.get(...)`.
   - Assert 200, `content-type: application/x-ndjson`, body is empty (or just `b""`).

7. **`test_follow_on_completed_run_closes_cleanly`**
   - Monkeypatch `_TAIL_POLL_SECONDS` in both modules to `0.01`.
   - Completed run.
   - `async with env.client.stream("GET", ..., params={"follow": "true"}) as resp:` — iterate to EOF.
   - Assert the iteration completes within `timeout_seconds=2.0` and yields all records.

### 2. Conftest tweak (if needed)
If monkeypatching module-level constants across the two files causes grief, add a small helper fixture:
```python
@pytest.fixture
def fast_tail_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.modules.ai.trace_jsonl._TAIL_POLL_SECONDS", 0.01)
    monkeypatch.setattr("app.modules.ai.service._TAIL_POLL_SECONDS", 0.01)
```
Place it in `tests/integration/conftest.py` (create if missing).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_trace_stream_route.py` | Create | 7 integration tests. |
| `tests/integration/conftest.py` | Create if missing | `fast_tail_poll` fixture. |

## Edge Cases & Risks
- `env.client.stream(...)` needs an `async with` — use it correctly so the httpx connection releases.
- The noop-backend test needs to override `get_trace_store` AFTER `integration_env` has already overridden it.  Use `env.app.dependency_overrides[get_trace_store] = lambda: NoopTraceStore()` inside the test body, before the request.
- Follow-mode integration tests need the `fast_tail_poll` fixture — without it the 200 ms idle polls make a terminal-run stream take ~1 s per test.
- Noop backend's `tail_run_stream(follow=True)` MUST return immediately (T-077's requirement) — if not, test #7 will hang.

## Acceptance Verification
- [ ] 7 test methods, all green.
- [ ] Total runtime < 5 s.
- [ ] Each test asserts on both headers and body.
