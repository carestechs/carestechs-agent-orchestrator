# Implementation Plan: T-147 — FEAT-005 ↔ FEAT-006 coexistence test

## Task Reference
- **Task ID:** T-147
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** AC-7 — FEAT-007 must not regress the FEAT-005 runtime, and DI changes must not interfere with the run-supervisor lifecycle.

## Overview
One test, one FastAPI app instance. Runs a FEAT-005 lifecycle-agent run to completion, then immediately drives a FEAT-006 14-signal flow. Asserts both terminate cleanly.

## Implementation Steps

### Step 1: Test module
**File:** `tests/integration/test_feat005_feat006_coexistence.py`
**Action:** Create

```python
@pytest.mark.asyncio
async def test_agent_run_then_signal_flow_in_same_process(
    client: AsyncClient, db_session: AsyncSession, stub_policy_factory, webhook_signer, respx_mock
):
    respx_mock.route(host="api.github.com").mock(return_value=httpx.Response(201, json={"id": 1}))

    # --- FEAT-005 path: lifecycle-agent run with stub policy ---
    policy = stub_policy_factory([("analyze_brief", {}), ("draft_plan", {}), ("terminate", {})])
    ...  # POST /api/v1/runs, wait for completion, assert Run.status == "completed"

    # --- FEAT-006 path: signal-driven lifecycle ---
    ...  # Reuse _lifecycle_helpers.drive_full_lifecycle(client, webhook_signer)
    # Assert WorkItem.status == "ready"
```

Scenarios to explicitly exercise:
- Run supervisor still alive after FEAT-006 signals complete.
- Second FEAT-005 run after the signal flow also completes (proves no DI leak).

### Step 2: Sanity-check the shared `httpx.AsyncClient`
**File:** `tests/integration/test_feat005_feat006_coexistence.py`
**Action:** Modify

After both flows, assert `_shared_http` from `app.core.github` is not closed (still usable for subsequent requests) and that the lifespan shutdown hook can close it idempotently.

### Step 3: Configure credentials
**File:** `tests/integration/test_feat005_feat006_coexistence.py`
**Action:** Modify

Set `GITHUB_PAT` to a fake value via `monkeypatch` so the factory picks `HttpxGitHubChecksClient` (not Noop). This is the "PAT configured but respx-mocked" scenario called out in the task. Use the conftest `_test_env` pattern.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_feat005_feat006_coexistence.py` | Create | Coexistence test. |
| `tests/integration/_lifecycle_helpers.py` | Modify | Extract any missing helpers shared with T-146. |

## Edge Cases & Risks
- **Run supervisor shutdown ordering.** If T-147 runs after a test that closes the supervisor, the FEAT-005 part hangs. Use function-scoped `app` fixture; verify supervisor is re-created per test.
- **Stub agent definition.** The test needs a minimal YAML agent at a known path. Use `tests/fixtures/agents/sample-linear.yaml` (already referenced in the README).
- **Respx catch-all.** Use `respx_mock.route(host="api.github.com").mock(return_value=...)` once, so both create and update calls get stubbed.

## Acceptance Verification
- [ ] Agent run completes (`Run.status == "completed"`).
- [ ] Signal flow reaches `WorkItem.status == "ready"`.
- [ ] Both happen inside a single test; no restart of the app.
- [ ] `uv run pytest tests/integration/test_feat005_feat006_coexistence.py` green.
