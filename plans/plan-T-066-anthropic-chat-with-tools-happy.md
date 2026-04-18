# Implementation Plan: T-066 — `AnthropicLLMProvider.chat_with_tools` one-shot happy path

## Task Reference
- **Task ID:** T-066
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-065

## Overview
Implement the single-turn happy path: translate orchestrator `ToolDefinition` into Anthropic's tool schema, call `messages.create(...)`, parse the `tool_use` block back into a `ToolCall`, and populate `Usage(input_tokens, output_tokens, latency_ms)` + a redacted `raw_response` dict.

## Steps

### 1. Modify `src/app/core/llm_anthropic.py`
- Add module-level constant `_RESPONSE_WHITELIST = frozenset({"id", "type", "role", "model", "stop_reason", "stop_sequence", "usage", "content"})` — keys we preserve in `raw_response`. Everything else (including any header echoes, internal metadata) is dropped.
- Add a pure helper `_to_anthropic_tools(tools: Sequence[ToolDefinition]) -> list[dict[str, Any]]`:
  ```python
  return [
      {"name": t.name, "description": t.description, "input_schema": t.parameters}
      for t in tools
  ]
  ```
- Add a pure helper `_redact_response(raw: dict[str, Any]) -> dict[str, Any]`:
  - Return `{k: raw[k] for k in _RESPONSE_WHITELIST if k in raw}`.
- Add a pure helper `_pick_tool_use(content: list[dict[str, Any]]) -> dict[str, Any] | list[dict[str, Any]]`:
  - Iterate and collect every block with `block.get("type") == "tool_use"`.
  - Return the list (length 0, 1, or >1). Caller decides what to do (T-067 maps 0/>1 → `PolicyError`). In this task's scope we just pick the first and assume length==1 — length validation arrives in T-067.
- Replace `chat_with_tools` body:
  ```python
  async def chat_with_tools(self, *, system, messages, tools):
      import time
      tool_schemas = _to_anthropic_tools(tools)
      started = time.perf_counter()
      response = await self._client.messages.create(
          model=self.model,
          max_tokens=self._max_tokens,
          system=system,
          messages=list(messages),
          tools=tool_schemas,
          tool_choice={"type": "auto"},
      )
      latency_ms = int((time.perf_counter() - started) * 1000)
      raw = response.model_dump(mode="json")
      redacted = _redact_response(raw)
      tool_uses = [b for b in redacted["content"] if b.get("type") == "tool_use"]
      # T-067 adds the 0/>1 branches; for now pick the first.
      first = tool_uses[0]
      usage = Usage(
          input_tokens=redacted["usage"]["input_tokens"],
          output_tokens=redacted["usage"]["output_tokens"],
          latency_ms=latency_ms,
      )
      return ToolCall(
          name=first["name"],
          arguments=first["input"],  # Anthropic: tool_use block has "input", not "arguments"
          usage=usage,
          raw_response=redacted,
      )
  ```
- Imports: add `from app.core.llm import Usage`.
- Remove the `raise NotImplementedYet(...)` stub.

### 2. Create `tests/modules/core/test_llm_anthropic_happy.py`
- Helper `_build_provider()` → `AnthropicLLMProvider` with a fake `sk-ant-xxx` key.
- Helper `_anthropic_response(tool_name="analyze_brief", tool_input=None, input_tokens=42, output_tokens=7)` → a dict matching Anthropic's Messages response shape with one `tool_use` content block.
- Tests under `respx.mock(base_url="https://api.anthropic.com")`:
  - `test_returns_tool_call_with_name_and_arguments` — 200 response with a tool_use block named `"analyze_brief"` and `input={"brief": "hi"}`. Assert `result.name == "analyze_brief"`, `result.arguments == {"brief": "hi"}`.
  - `test_usage_populated` — assert `result.usage.input_tokens == 42`, `result.usage.output_tokens == 7`, `result.usage.latency_ms > 0`.
  - `test_tool_translation_uses_input_schema_key` — parse the outbound request body, assert `request_json["tools"][0] == {"name": "...", "description": "...", "input_schema": {...}}` (NOT `"parameters"`).
  - `test_system_and_messages_forwarded_verbatim` — pass `system="SYS"`, `messages=[{"role": "user", "content": "hello"}]`; assert outbound payload carries them byte-identical.
  - `test_raw_response_contains_expected_keys` — assert `result.raw_response.keys() <= {"id", "type", "role", "model", "stop_reason", "stop_sequence", "usage", "content"}`.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/core/llm_anthropic.py` | Modify | Happy-path body + helpers. |
| `tests/modules/core/test_llm_anthropic_happy.py` | Create | 5 respx-mocked happy tests. |

## Edge Cases & Risks
- Anthropic's content blocks can include `text` blocks before a `tool_use` block. The filter `[b for b in content if b["type"] == "tool_use"]` skips them correctly — assert this in a test variant (response with a leading text block + one tool_use block still returns the tool call).
- `response.model_dump(mode="json")` is SDK-version-sensitive; if a future SDK upgrade changes the shape, the whitelist + test fixtures catch it.
- `tool_use.input` vs. orchestrator's `tool_call.arguments` — they are the same thing under different names. The provider normalizes to `arguments`.

## Acceptance Verification
- [ ] Tool-use response → `ToolCall` with matching name and arguments.
- [ ] `Usage` populated with Anthropic's token counts + measured `latency_ms > 0`.
- [ ] Outbound request has `"input_schema"` (not `"parameters"`) per tool.
- [ ] `system` + `messages` forwarded byte-identical.
- [ ] `raw_response` keys are a subset of the whitelist.
- [ ] `uv run pyright` + `uv run ruff check .` clean.
