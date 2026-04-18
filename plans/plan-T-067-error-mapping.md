# Implementation Plan: T-067 — Error mapping at the provider boundary

## Task Reference
- **Task ID:** T-067
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-066

## Overview
Wrap the happy-path API call in a single `try/except` that converts SDK-native exceptions and anomalous response shapes into the orchestrator's typed errors (`ProviderError` for transport + API failures, `PolicyError` for response-shape policy violations). Extend `ProviderError` to carry HTTP-level forensic fields in parallel to `EngineError`.

## Steps

### 1. Modify `src/app/core/exceptions.py`
- Update `ProviderError`:
  ```python
  class ProviderError(AppError):
      code = "provider-error"
      http_status = 502
      title = "LLM provider error"

      def __init__(
          self,
          detail: str,
          *,
          errors: dict[str, list[str]] | None = None,
          provider_http_status: int | None = None,
          provider_request_id: str | None = None,
          original_body: str | None = None,
      ) -> None:
          super().__init__(detail, errors=errors)
          self.provider_http_status = provider_http_status
          self.provider_request_id = provider_request_id
          self.original_body = original_body
  ```
- Backward compatibility: existing callers use `ProviderError("msg")` with no kwargs — they still work because the three new kwargs default to `None`.

### 2. Modify `src/app/core/llm_anthropic.py`
- After `_pick_tool_use` extraction in `chat_with_tools`, branch on `len(tool_uses)`:
  - `0` → `raise PolicyError("policy selected no tool")` with a hint if the Anthropic `stop_reason == "max_tokens"`: `"policy selected no tool (stop_reason=max_tokens — raise anthropic_max_tokens or tighten the prompt)"`.
  - `>1` → `raise PolicyError(f"policy selected multiple tools: {[t['name'] for t in tool_uses]}")`.
  - `1` → existing behavior.
- Wrap the entire `messages.create` invocation (not the response parsing) in try/except:
  ```python
  try:
      response = await self._client.messages.create(...)
  except anthropic.APIStatusError as exc:
      raise ProviderError(
          f"Anthropic returned HTTP {exc.status_code}",
          provider_http_status=exc.status_code,
          provider_request_id=_request_id(exc),
          original_body=exc.response.text if exc.response is not None else None,
      ) from exc
  except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
      raise ProviderError(
          f"Anthropic transport failure: {exc}",
          provider_http_status=None,
          provider_request_id=None,
          original_body=None,
      ) from exc
  ```
- Add a local helper `_request_id(exc: anthropic.APIStatusError) -> str | None`:
  - Try `exc.request_id` (SDK attribute on current versions).
  - Fallback: `exc.response.headers.get("request-id")` then `exc.response.headers.get("x-request-id")` if `exc.response` is not None.
  - Return `None` if nothing matches.
- Imports: add `from app.core.exceptions import PolicyError, ProviderError` at the top (PolicyError already exists).

### 3. Create `tests/modules/core/test_llm_anthropic_errors.py`
- Helper `_http_error_response(status, body, headers)` used by respx.
- Tests:
  - `test_500_raises_provider_error_with_status_and_request_id` — 500 response with header `request-id: req-xyz`. Assert `exc.provider_http_status == 500`, `exc.provider_request_id == "req-xyz"`, `exc.original_body == <body>`.
  - `test_401_raises_provider_error` — assert `exc.provider_http_status == 401`.
  - `test_connect_error_raises_provider_error_with_none_status` — respx `side_effect=httpx.ConnectError(...)` (Anthropic SDK wraps this). Assert `exc.provider_http_status is None`.
  - `test_timeout_raises_provider_error` — similar with `httpx.ReadTimeout`.
  - `test_zero_tool_use_blocks_raises_policy_error` — 200 response with only `text` content, no `tool_use`. Assert `PolicyError("policy selected no tool")` (str match on the message).
  - `test_max_tokens_stop_reason_includes_hint` — 200 with `stop_reason="max_tokens"` and no tool_use. Assert the PolicyError message contains `"max_tokens"`.
  - `test_multiple_tool_use_blocks_raises_policy_error` — 200 with two tool_use blocks. Assert `PolicyError` message names both tools.
- Run under `respx.mock(base_url="https://api.anthropic.com")`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/core/exceptions.py` | Modify | Extend `ProviderError` signature. |
| `src/app/core/llm_anthropic.py` | Modify | try/except + PolicyError branches. |
| `tests/modules/core/test_llm_anthropic_errors.py` | Create | 7 respx-mocked error tests. |

## Edge Cases & Risks
- The Anthropic SDK might raise `httpx.ConnectError` directly (not wrapped). Catch both `anthropic.APIConnectionError` and bare `httpx.ConnectError` / `httpx.TimeoutException` for robustness — but prefer the SDK's own wrappers, which newer versions emit.
- `exc.response` can be `None` on transport errors; guard both `response.text` and `response.headers` accesses.
- `request_id` field location varies across SDK versions; the fallback chain covers known ones.
- `PolicyError` is an existing exception — do not re-define it.

## Acceptance Verification
- [ ] 5xx and 4xx both raise `ProviderError` with populated `provider_http_status`.
- [ ] Connection / timeout raise `ProviderError(provider_http_status=None)`.
- [ ] Zero tool_use → `PolicyError("policy selected no tool")`.
- [ ] `stop_reason="max_tokens"` → PolicyError message includes the hint.
- [ ] Two tool_use blocks → PolicyError with both names in the message.
- [ ] `ProviderError` gains `provider_http_status`, `provider_request_id`, `original_body` (nullable).
- [ ] Existing callers that do `ProviderError("msg")` still work.
- [ ] `uv run pyright` + `uv run ruff check .` clean.
