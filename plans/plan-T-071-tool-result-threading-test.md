# Implementation Plan: T-071 — Multi-turn tool-result threading test

## Task Reference
- **Task ID:** T-071
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-066

## Overview
Prove the provider forwards `messages` byte-identical across turns — a critical invariant because Anthropic's `tool_use` / `tool_result` pairing is easy to break. The test does not change production code; it pins the provider's "no transformation" contract.

## Steps

### 1. Create `tests/modules/core/test_llm_anthropic_threading.py`
- Helper `_tool_use_response(tool_use_id, name, input)` returns an Anthropic 200 response dict with one `tool_use` content block that uses the given id.
- Test `test_two_turns_thread_tool_use_and_tool_result`:
  - Set up a respx route with two sequential responses (first turn returns `tool_use_id="tu_1"` for `"analyze_brief"`; second turn returns `tool_use_id="tu_2"` for `"draft_plan"`).
  - First call: `chat_with_tools(system="SYS", messages=[{"role": "user", "content": "start"}], tools=[analyze, draft])` → returns `ToolCall(name="analyze_brief")`.
  - Capture `tool_use_id` from the first turn's returned `raw_response` content (`raw_response["content"][0]["id"]`).
  - Build the turn-2 message list:
    ```python
    messages_turn2 = [
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": first.raw_response["content"]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": '{"ok": true}'},
        ]},
    ]
    ```
  - Second call: `chat_with_tools(system="SYS", messages=messages_turn2, tools=[analyze, draft])`.
  - Assert the outbound request body for the second call contains `messages_turn2` **verbatim** — the provider does not mutate, reorder, or drop any entries.
  - Assert `tool_use_id` in the second turn's `tool_result` matches the id returned in the first turn's `tool_use` (forensic correlation).
- Test `test_provider_does_not_add_terminate_tool`:
  - Call with `tools=[analyze_brief_only]` (no `terminate`). Assert the outbound `tools` list length is 1 and does NOT include any tool named `"terminate"`. (Rationale: the `terminate` tool is added by `runtime.tools.build_tools`, not by the provider.)

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/modules/core/test_llm_anthropic_threading.py` | Create | Multi-turn pinning tests. |

## Edge Cases & Risks
- respx's response ordering: use `respx.mock(...)` with `side_effect=[resp1, resp2]` OR `route.mock(return_value=...)` called twice if that's cleaner in the current respx version. The FEAT-002 test suite uses `side_effect`; prefer consistency.
- `tool_use_id` format is SDK-internal (typically `"toolu_…"`); we use the arbitrary test id `"tu_1"` in the fixture so the assertion is deterministic.
- If future runtime work plumbs actual multi-turn conversations, this test stays valid — it's an invariant on the provider, not on the runtime.

## Acceptance Verification
- [ ] Two-turn test passes; second outbound payload matches assembled message list byte-for-byte.
- [ ] `tool_use_id` correlation asserted.
- [ ] Provider does not inject `terminate` or any other tool beyond the caller-supplied list.
- [ ] `uv run pytest tests/modules/core/test_llm_anthropic_threading.py` green.
