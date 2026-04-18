# Implementation Plan: T-072 — End-to-end runtime driven by `AnthropicLLMProvider` (respx-mocked)

## Task Reference
- **Task ID:** T-072
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** L
- **Dependencies:** T-069, T-071

## Overview
Mirror FEAT-002's `test_run_end_to_end::test_linear_agent_completes_with_done_node` but with a real `AnthropicLLMProvider` (respx-mocked at the SDK's HTTP boundary) instead of `StubLLMProvider`. Proves AC-3: same runtime behavior, same downstream rows, regardless of which provider drives the decisions.

## Steps

### 1. Modify `tests/integration/env.py`
- Add a new keyword-only parameter to `integration_env(...)`: `policy: LLMProvider | None = None`. Default `None` preserves existing behavior.
- Inside the context, if `policy is None`, build the usual `StubLLMProvider(list(policy_script))`. Otherwise use the provided `policy` and ignore `policy_script`.
- Update the `get_llm_provider_dep` override:
  ```python
  app.dependency_overrides[get_llm_provider_dep] = lambda: resolved_policy
  ```
  where `resolved_policy` is either the built stub or the caller-provided one.
- Document in the docstring that `policy` overrides `policy_script` when both are set.

### 2. Create `tests/integration/test_run_end_to_end_anthropic.py`
- Helper `_anthropic_tool_use_response(tool_name, tool_input={}, input_tokens=25, output_tokens=10)` → Anthropic 200 response dict with one `tool_use` content block. Use a stable fake `tool_use` id per turn so the test is deterministic.
- Test `test_linear_agent_completes_under_anthropic_provider`:
  - Setup: `agents_dir = prepare_agents_dir(tmp_path / "agents")`, `trace_dir = tmp_path / "trace"`.
  - Build an `AnthropicLLMProvider` directly (not via the factory) with fake key `sk-ant-test-xxx-yyy`.
  - Under `respx.mock(base_url="https://api.anthropic.com")`:
    - 3 sequential responses — `analyze_brief`, `draft_plan`, `review_plan` (terminal).
  - `integration_env(engine, ..., policy=provider, policy_script=[])`:
    - POST `/api/v1/runs` with `{"agentRef": "sample-linear@1.0", "intake": {"brief": "hi"}}`.
    - `poll_until_terminal(env, run_id, timeout_seconds=5.0)`.
  - Assertions mirror FEAT-002's e2e test:
    - `run.status == COMPLETED`, `run.stop_reason == DONE_NODE`.
    - 3 `Step` rows (`analyze_brief`, `draft_plan`, `review_plan`), all `COMPLETED`.
    - 3 `PolicyCall` rows; every `provider == "anthropic"`; every `input_tokens > 0`.
    - `RunMemory.data` contains merged results.
    - `.trace/<run_id>.jsonl` exists with ≥ 3 step + 3 policy + 3 webhook lines.
  - Respx route hit count == 3.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/env.py` | Modify | Add optional `policy` parameter. |
| `tests/integration/test_run_end_to_end_anthropic.py` | Create | Anthropic e2e composition-integrity test. |

## Edge Cases & Risks
- The runtime loop builds `tools = build_tools(agent, tool_names)` which adds `terminate` as the last tool. Anthropic's mocked responses MUST return one of the named tools (`analyze_brief` / `draft_plan` / `review_plan`) — not `terminate` — so the run ends via `done_node`, not `policy_terminated` (as per T-054's stop-reason ordering).
- The Anthropic SDK posts to `https://api.anthropic.com/v1/messages`. Confirm the respx base URL matches (it does by default).
- The run-loop calls `chat_with_tools` once per iteration. With 3 iterations we need 3 respx responses. If the respx route runs out of responses, the 4th call would fail — but we only expect 3. Assert `route.call_count == 3`.
- The provider's retry logic (T-068) could re-run a 5xx response; our fixtures should return 200s so retries don't fire. Keep the mocks pure-200.
- `integration_env` already overrides `get_llm_provider_dep`; the new `policy` parameter threads through that existing wiring without extra plumbing.

## Acceptance Verification
- [ ] Test completes in < 5s (timeout bound).
- [ ] `run.status == COMPLETED`, `run.stop_reason == DONE_NODE`.
- [ ] 3 Steps all `COMPLETED`; 3 PolicyCalls with `provider == "anthropic"`.
- [ ] Every `PolicyCall.input_tokens > 0` (from the mocked usage blocks).
- [ ] JSONL trace file exists and reads back cleanly.
- [ ] Respx assertions: exactly 3 calls to `api.anthropic.com/v1/messages`.
- [ ] `uv run pytest tests/integration/` stays green (no regression on stub e2e).
