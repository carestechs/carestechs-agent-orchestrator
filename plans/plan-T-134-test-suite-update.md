# Implementation Plan: T-134 — Test suite update + E2E rework

## Task Reference
- **Task ID:** T-134
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** L
- **Dependencies:** T-133

## Overview
Every FEAT-006 test assumed state in the orchestrator's Postgres and immediate synchronous writes inside signal handlers. The engine-backed implementation makes state writes asynchronous (via engine webhook). Tests need to either mock the engine at the client boundary or drive a synthetic webhook.

## Steps

### 1. Create `tests/modules/ai/lifecycle/fixtures.py`
- `RecordingEngineClient` — a fake `FlowEngineLifecycleClient` that records every method call; `create_item`, `transition_item`, `create_workflow`, `ensure_webhook` all return plausible UUIDs.
- `synthetic_engine_webhook(client, correlation_id, item_id, from_status, to_status)` — helper that POSTs `/hooks/engine/lifecycle/item-transitioned` with a valid HMAC signature (reuse `WEBHOOK_SECRET`).

### 2. Update `tests/modules/ai/lifecycle/test_work_items.py`
- Every test now:
  - Uses `RecordingEngineClient` injected via `app.dependency_overrides`.
  - Asserts on `engine.calls['transition_item']` shape instead of direct `status` read.
  - For tests that verify derivations (W2, W5), the test drives `synthetic_engine_webhook(...)` and then asserts the reactor's side effects.

### 3. Update `tests/modules/ai/lifecycle/test_tasks.py`
- Same pattern. Rejection tests no longer need to mock the engine (rejections skip the engine) — assert `Approval` row written inline.

### 4. Update `tests/modules/ai/test_router_*.py`
- For each signal endpoint:
  - Override engine client to recording fake.
  - POST the signal; assert (a) engine was called with right params + correlation id, (b) `PendingSignalContext` row present.
  - POST the synthetic webhook; assert (c) aux rows written, (d) `PendingSignalContext` row deleted.

### 5. Update `tests/integration/test_feat006_e2e.py`
- Use `RecordingEngineClient` + synthetic webhooks.
- All 14 signals still exercised; now each signal is a pair: HTTP call + synthetic webhook.

### 6. Create `tests/integration/test_feat006_e2e_real_engine.py`
- Opt-in via `@pytest.mark.requires_engine`.
- Spins up a real `FlowEngineLifecycleClient` pointed at a running `carestechs-flow-engine` instance (env vars: `TEST_FLOW_ENGINE_BASE_URL`, `TEST_FLOW_ENGINE_TENANT_KEY`).
- Drives the same scenario as `test_feat006_e2e.py` but against the real engine.
- Skips with a clear message if env vars aren't set.

### 7. Update `tests/conftest.py`
- Add `--run-requires-engine` CLI flag gating `requires_engine` marker.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/modules/ai/lifecycle/fixtures.py` | Create | Recording client + webhook helper. |
| `tests/modules/ai/lifecycle/test_*.py` | Modify | All. |
| `tests/modules/ai/test_router_*.py` | Modify | All. |
| `tests/integration/test_feat006_e2e.py` | Modify | Use recording client + webhooks. |
| `tests/integration/test_feat006_e2e_real_engine.py` | Create | Opt-in real-engine smoke. |
| `tests/conftest.py` | Modify | New marker flag. |

## Edge Cases & Risks
- **Async drift.** In production, engine → webhook has network latency; in tests we drive the webhook synchronously after the signal. Tests will pass in unrealistic-timing conditions; the real-engine test covers the realistic path.
- **Webhook signature helper.** Reuse existing `sign_body` — the engine's webhook secret can match the orchestrator's for v1 (they're the same tenant).
- **Docker-compose coordination.** The real-engine test needs both services up. Document in README; gated by env var so CI doesn't flake.

## Acceptance Verification
- [ ] All FEAT-006 unit + route + integration tests green against engine-backed impl.
- [ ] Opt-in real-engine E2E test present and runs locally.
- [ ] No test writes directly to `work_items.status` / `tasks.status`.
- [ ] `uv run pytest tests/modules tests/integration` green.
