# Implementation Plan: T-036 — `FlowEngineClient.dispatch_node` full implementation

## Task Reference
- **Task ID:** T-036
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-031

## Overview
Replace the `NotImplementedYet` body with a real HTTP dispatch. Wraps all httpx errors into `EngineError` with correlation metadata. Establishes the outbound engine contract.

## Steps

### 1. Modify `src/app/config.py`
- Add `engine_dispatch_timeout_seconds: int = 10`.
- Add `public_base_url: AnyHttpUrl` (required — the engine must be able to reach us back for webhooks).

### 2. Modify `src/app/modules/ai/engine_client.py`
- `FlowEngineClient.__init__` now captures `settings.engine_dispatch_timeout_seconds` and `settings.public_base_url`.
- Replace `dispatch_node` stub. New signature:
  ```python
  async def dispatch_node(
      self,
      *,
      run_id: uuid.UUID,
      step_id: uuid.UUID,
      agent_ref: str,
      node_name: str,
      node_inputs: dict[str, Any],
  ) -> str:
  ```
- Body:
  1. Build `callback_url = f"{self._public_base_url.rstrip('/')}/hooks/engine/events"`.
  2. Build payload: `{"agentRef": agent_ref, "runId": str(run_id), "stepId": str(step_id), "nodeName": node_name, "nodeInputs": node_inputs, "callbackUrl": callback_url}`.
  3. Call `await self._request("POST", "/nodes/dispatch", json=payload, timeout=self._dispatch_timeout)` — `_request` already wraps httpx errors into `EngineError`.
  4. Parse response JSON, extract `engineRunId` (raise `EngineError(detail="engine response missing engineRunId")` if absent).
  5. Return the `engineRunId` string.
- Add `timeout` keyword forwarding in `_request` if not already supported.

### 3. Create `tests/modules/ai/test_engine_client_dispatch.py` (extends T-017 coverage)
- Using `respx`:
  - 200 with `{"engineRunId": "e-123"}` → returns `"e-123"`.
  - 500 status → raises `EngineError` with `engine_http_status=500` and populated `original_body`.
  - 400 with `x-correlation-id` header → raised `EngineError.engine_correlation_id == header value`.
  - `httpx.ConnectError` → `EngineError(engine_http_status=None)`.
  - `asyncio.TimeoutError` (set timeout=0.001 + delay mock) → `EngineError(engine_http_status=None)`.
  - Payload assertion: outbound body has all documented keys + Bearer header when `engine_api_key` set.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/config.py` | Modify | `engine_dispatch_timeout_seconds`, `public_base_url`. |
| `src/app/modules/ai/engine_client.py` | Modify | Real `dispatch_node` body. |
| `tests/modules/ai/test_engine_client_dispatch.py` | Create | Parameterized respx tests. |

## Edge Cases & Risks
- Engine's real API shape is assumed (see FEAT-002 §Risks-1). If the contract differs when the engine stabilizes, update the payload builder here and add a contract test under `tests/contract/` marked `@pytest.mark.live`.
- `public_base_url` inside Docker networks: document that it must be the URL the engine reaches, not `localhost`.
- Response parsing should be lenient — future engine versions may add fields; only `engineRunId` is required.

## Acceptance Verification
- [ ] 202/200 happy path returns `engine_run_id`.
- [ ] 5xx path raises `EngineError` with correct metadata.
- [ ] Connection + timeout errors wrap into `EngineError` (never leak raw `httpx`).
- [ ] Payload shape documented in the method docstring.
- [ ] `uv run pytest tests/modules/ai/test_engine_client_dispatch.py -v` green.
