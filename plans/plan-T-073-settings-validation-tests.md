# Implementation Plan: T-073 — Settings validation tests

## Task Reference
- **Task ID:** T-073
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-063

## Overview
Dedicated parameterized test class covering the T-063 settings validator. Each case constructs `Settings(...)` directly (no env var reliance) to avoid cross-test leakage.

## Steps

### 1. Modify `tests/test_config.py`
- Add a new test class `TestAnthropicValidation` that imports `pydantic.ValidationError` and `app.config.Settings`.
- Helper `_required_kwargs()` returning the non-anthropic required fields as a dict:
  ```python
  def _required_kwargs() -> dict[str, Any]:
      return {
          "database_url": "postgresql+asyncpg://u:p@h:5432/db",
          "orchestrator_api_key": "k",
          "engine_webhook_secret": "s",
          "engine_base_url": "http://engine.test",
      }
  ```
- Cases (each a separate method):
  1. `test_anthropic_provider_missing_key_raises` — `Settings(**_required_kwargs(), llm_provider="anthropic")` (no `anthropic_api_key`) → `ValidationError` whose message contains `"anthropic_api_key"`.
  2. `test_anthropic_provider_empty_key_raises` — same but `anthropic_api_key=""` → ValidationError (tests the `.strip() == ""` branch).
  3. `test_anthropic_provider_whitespace_key_raises` — `anthropic_api_key="   "` → ValidationError.
  4. `test_anthropic_provider_valid_key_succeeds` — `anthropic_api_key="sk-ant-test"` → Settings constructs OK.
  5. `test_stub_provider_without_key_succeeds` — `llm_provider="stub"`, no key → Settings constructs OK (regression).
  6. `test_anthropic_provider_defaults_model` — `llm_provider="anthropic"`, `anthropic_api_key="sk-ant-test"`, no `llm_model` → `settings.llm_model == "claude-opus-4-7"`.
  7. `test_anthropic_provider_respects_explicit_model` — same but `llm_model="claude-sonnet-4-6"` → `settings.llm_model == "claude-sonnet-4-6"`.
  8. `test_anthropic_max_tokens_zero_rejected` — `anthropic_max_tokens=0` → ValidationError.
  9. `test_anthropic_timeout_seconds_negative_rejected` — `anthropic_timeout_seconds=-1` → ValidationError.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/test_config.py` | Modify | New `TestAnthropicValidation` class with 9 cases. |

## Edge Cases & Risks
- The session-scoped `_test_env` fixture in `conftest.py` sets env vars globally. `Settings(...)` with explicit kwargs overrides env for those keys, but `ANTHROPIC_API_KEY` might leak from env if it's set in CI. Use `monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)` at the top of every method to be safe.
- Pydantic v2 wraps field-validator errors inside `ValidationError`; the message-match assertion MUST use `str(exc.value)` or check `.errors()` — both are valid; pick one and be consistent.
- `SecretStr` accepts bare strings and `SecretStr("…")`; our validator reads `.get_secret_value()` so both work.

## Acceptance Verification
- [ ] All 9 cases pass.
- [ ] No env-var leakage (tests pass regardless of `ANTHROPIC_API_KEY` being set in the shell).
- [ ] `uv run pytest tests/test_config.py` green.
- [ ] `uv run pyright` + `uv run ruff check .` clean.
