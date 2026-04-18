# Implementation Plan: T-074 — Secret-never-leaks test

## Task Reference
- **Task ID:** T-074
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-069

## Overview
One integration-level test that runs a provider call with a sentinel-bearing fake API key, then reads back every potential leak surface (`PolicyCall.raw_response`, the JSONL trace file, captured logs) and asserts the sentinel string does not appear anywhere. A cheap, forever guardrail.

## Steps

### 1. Create `tests/integration/test_anthropic_secret_redaction.py`
- Sentinel constant: `_SECRET = "sk-ant-SECRET_MARKER_test_only_" + "x" * 40` — long enough to satisfy the shape check.
- Helper `_anthropic_tool_use_response(tool_name)` same as T-072.
- Test `test_api_key_never_appears_in_trace_policy_call_or_logs`:
  - Build an `AnthropicLLMProvider` with `Settings(llm_provider="anthropic", anthropic_api_key=_SECRET, ...)`.
  - Use `integration_env(..., policy=provider, policy_script=[])` as in T-072.
  - Under `respx.mock(base_url="https://api.anthropic.com")` with a single `tool_use` response naming `"analyze_brief"`.
  - Start `caplog.set_level(logging.DEBUG, logger="app.core.llm_anthropic")` plus `"app.modules.ai"`.
  - POST `/api/v1/runs`; `poll_until_terminal(env, run_id)`.
  - Assertions:
    1. **DB**: `SELECT raw_response FROM policy_calls WHERE run_id = …` → `_SECRET not in json.dumps(raw_response)`.
    2. **Trace file**: read `.trace/<run_id>.jsonl` → `_SECRET not in file_contents`.
    3. **Logs**: `_SECRET not in caplog.text`.
  - All three assertions MUST use the full `_SECRET` string (not just `"SECRET_MARKER"`) so partial-leak paths are also caught.
- One iteration is enough to populate one PolicyCall + three trace kinds (step/policy_call/webhook_event). The run exhausts its "script" after one call and terminates via `policy_error` (stop_reason=error) — that's fine; we're testing leakage, not completion.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_anthropic_secret_redaction.py` | Create | Single redaction guardrail test. |

## Edge Cases & Risks
- The Anthropic SDK uses the key as an HTTP header (`x-api-key`); respx captures request headers. Our test does NOT assert on request headers (the key must be there for the SDK to work). We assert only on what the orchestrator persists — traces, logs, DB.
- caplog's default propagation may miss logger hierarchy — explicitly set the level on `"app"` or the specific logger names.
- The autouse `_test_env` fixture in `conftest.py` may override `ANTHROPIC_API_KEY`; that's fine — our provider is constructed with an explicit `Settings` object carrying `_SECRET`, bypassing env.

## Acceptance Verification
- [ ] `_SECRET` does NOT appear in `PolicyCall.raw_response` (JSON-dumped).
- [ ] `_SECRET` does NOT appear in the JSONL trace file.
- [ ] `_SECRET` does NOT appear in captured logs.
- [ ] Test passes under `uv run pytest tests/integration/test_anthropic_secret_redaction.py`.
