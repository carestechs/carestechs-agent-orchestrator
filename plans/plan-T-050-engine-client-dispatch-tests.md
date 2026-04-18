# Implementation Plan: T-050 — Engine-client dispatch tests

## Task Reference
- **Task ID:** T-050
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-036

## Overview
Parameterized `respx` tests extending T-036's basic coverage with the full matrix of outcomes: happy, status errors, connection error, timeout, header parsing.

## Steps

### 1. Extend `tests/modules/ai/test_engine_client_dispatch.py`

Add:
- Outbound payload assertion: respx `route.calls[0].request` body matches expected shape (`agentRef`, `runId`, `stepId`, `nodeName`, `nodeInputs`, `callbackUrl`). Verify via `json.loads(request.content)`.
- Auth header: when `settings.engine_api_key` is set, outbound request has `Authorization: Bearer <token>`. When unset, no such header.
- Correlation-id parsing:
  - 400 response with `x-correlation-id: abc-123` header → raised `EngineError.engine_correlation_id == "abc-123"`.
  - 500 response without header → `engine_correlation_id is None`.
- Original body preservation: raised `EngineError.original_body` == response text.
- Connection error via `respx.get(...).mock(side_effect=httpx.ConnectError(...))` → `EngineError(engine_http_status=None, engine_correlation_id=None, original_body=None)`.
- Timeout: `httpx.ReadTimeout` similarly wrapped.
- Missing `engineRunId` in response JSON → `EngineError(detail="engine response missing engineRunId")`.

### 2. (Optional) Create `tests/contract/test_engine_dispatch_contract.py`

- Marked `@pytest.mark.live` — hits a real engine instance if configured.
- Uses `pytest.skipif(not os.getenv("ENGINE_LIVE_URL"), ...)` guard.
- Exists as a placeholder so a contract sweep can be wired into a scheduled CI job later.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/modules/ai/test_engine_client_dispatch.py` | Modify | Extended matrix. |
| `tests/contract/test_engine_dispatch_contract.py` | Create (optional) | Live-marker placeholder. |

## Edge Cases & Risks
- Timeouts in CI: don't use `time.sleep`; use `respx` delays instead to keep tests fast and deterministic.
- Connection-error simulation differs across respx versions — prefer `side_effect` over `route.mock(status_code=599)` which may not trigger ConnectError semantics.

## Acceptance Verification
- [ ] 5+ parameterized outcomes all green.
- [ ] Outbound payload shape asserted explicitly.
- [ ] Auth header presence/absence asserted.
- [ ] Correlation-id propagation works.
- [ ] Live-marker contract test defined (skipped by default).
