# Implementation Plan: T-069 — Factory wiring + API-key redaction

## Task Reference
- **Task ID:** T-069
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-066

## Overview
Flip the `get_llm_provider` factory's anthropic branch from `raise NotImplementedYet` to `return AnthropicLLMProvider(settings)`. Also add an explicit test that the API key never appears in `raw_response` dicts (the whitelist from T-066 already enforces this, but the test is the guardrail).

## Steps

### 1. Modify `src/app/core/llm.py`
- Replace the anthropic branch in `get_llm_provider`:
  ```python
  case "anthropic":
      from app.core.llm_anthropic import AnthropicLLMProvider

      return AnthropicLLMProvider(settings)
  ```
- Keep the deferred import inside the `case` — avoids pulling `anthropic` into the general import path for stub-only deployments.
- Update the `NotImplementedYet` import if it's no longer referenced.

### 2. Create `tests/modules/core/test_llm_anthropic_factory.py`
- `test_factory_returns_anthropic_provider_when_selected` — build `Settings(llm_provider="anthropic", anthropic_api_key="sk-ant-test", ...)`, call `get_llm_provider(settings)`, assert the returned instance is `AnthropicLLMProvider`.
- `test_factory_returns_stub_provider_when_selected` — build `Settings(llm_provider="stub")`, call `get_llm_provider(settings)`, assert `StubLLMProvider` (regression guard for the default path).
- `test_unknown_provider_raises_provider_error` — pass a settings-like object whose `llm_provider` is `"made-up"`. Assert `ProviderError`.
- `test_raw_response_does_not_contain_api_key` — build an `AnthropicLLMProvider` with `anthropic_api_key="sk-ant-SECRET_MARKER_test"`. Under respx, return a happy 200 response and invoke `chat_with_tools`. Assert `"sk-ant-SECRET_MARKER"` not in `json.dumps(result.raw_response)`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/core/llm.py` | Modify | Factory branch flip. |
| `tests/modules/core/test_llm_anthropic_factory.py` | Create | 4 factory tests. |

## Edge Cases & Risks
- The factory currently takes `settings: object` (to avoid importing `Settings` at the type level). Keep that — we use `getattr(settings, "llm_provider", "stub")` so the test for the unknown-provider case can pass a small namespace-like object.
- `NotImplementedYet` may still be imported if used elsewhere — check before removing the import.
- The redaction test uses `"sk-ant-SECRET_MARKER"` as a sentinel to make accidental leaks obvious in the failure message. The full token T-074 lands in integration scope.

## Acceptance Verification
- [ ] `get_llm_provider(Settings(llm_provider="anthropic", ...))` → `AnthropicLLMProvider`.
- [ ] `get_llm_provider(Settings(llm_provider="stub"))` → `StubLLMProvider`.
- [ ] Unknown provider → `ProviderError`.
- [ ] `raw_response` under any happy call never contains the API key substring.
- [ ] Full suite still green (stub path unchanged).
- [ ] `uv run pyright` + `uv run ruff check .` clean.
