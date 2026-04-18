# Implementation Plan: T-075 — Live contract test

## Task Reference
- **Task ID:** T-075
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-069

## Overview
A real API round-trip under `@pytest.mark.live`, skipped by default. Proves the full happy path works against the real Anthropic endpoint. Exists so a scheduled CI job can validate on a cadence without forcing every local run to hit the network.

## Steps

### 1. Create `tests/contract/test_anthropic_provider_contract.py`
- Imports: `import os`, `import pytest`, `from app.config import Settings`, `from app.core.llm_anthropic import AnthropicLLMProvider`, `from app.core.llm import ToolDefinition`.
- Skip guard:
  ```python
  def _skip_if_no_live() -> None:
      if not os.environ.get("ANTHROPIC_API_KEY"):
          pytest.skip("set ANTHROPIC_API_KEY to run the live Anthropic contract test")
  ```
- Test `test_chat_with_tools_roundtrip`:
  - `@pytest.mark.live` + `@pytest.mark.asyncio(loop_scope="function")`.
  - `_skip_if_no_live()` at the top.
  - Build `Settings(llm_provider="anthropic", anthropic_api_key=os.environ["ANTHROPIC_API_KEY"], database_url="postgresql+asyncpg://u:p@h:5432/unused", orchestrator_api_key="unused", engine_webhook_secret="unused", engine_base_url="http://unused.test")`.
  - Build `provider = AnthropicLLMProvider(settings)`.
  - `echo_tool = ToolDefinition(name="echo", description="Echo back the provided text", parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]})`.
  - Call `result = await provider.chat_with_tools(system="You MUST call the echo tool with text='hello'. Do not call any other tool. Do not emit plain text.", messages=[{"role": "user", "content": "Please echo 'hello'."}], tools=[echo_tool])`.
  - Assertions:
    - `result.name == "echo"`.
    - `result.arguments.get("text") == "hello"` (case-insensitive match on the string value is acceptable — models occasionally normalize).
    - `result.usage.input_tokens > 0`.
    - `result.usage.output_tokens > 0`.
    - `result.raw_response is not None and "id" in result.raw_response`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/contract/test_anthropic_provider_contract.py` | Create | Single live round-trip test. |

## Edge Cases & Risks
- Without `--run-live` (see `tests/conftest.py`), `@pytest.mark.live` is auto-skipped by the collection hook. We don't need a second `skipif` for that; the env-var guard covers the complementary case (live run without a key).
- Claude may normalize `'hello'` → `hello` (quotes stripped). The arguments assertion allows either substring match or direct equality — use `result.arguments.get("text", "").lower().strip("'\"") == "hello"` for robustness.
- Anthropic sometimes returns a `text` content block before the `tool_use` block. `_pick_tool_use` from T-066 skips texts correctly.
- Test MUST finish in under 10 s on a healthy connection; Anthropic's P95 for short prompts is well under 3 s.
- Do NOT hardcode the API key — read from env every time.

## Acceptance Verification
- [ ] Test skipped when `--run-live` is not passed.
- [ ] Test skipped when `ANTHROPIC_API_KEY` is absent (even with `--run-live`).
- [ ] With both in place, exactly one request hits `https://api.anthropic.com/v1/messages`.
- [ ] Assertions on `result.name`, `result.arguments`, token counts, `raw_response` all pass.
- [ ] Completes in under 10 s.
