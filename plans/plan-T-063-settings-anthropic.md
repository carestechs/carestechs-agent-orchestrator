# Implementation Plan: T-063 — Settings: required Anthropic fields + validation

## Task Reference
- **Task ID:** T-063
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** None

## Overview
Tighten `Settings` so a process configured with `llm_provider=anthropic` but no API key fails at `Settings()` construction rather than on first request. Add two Anthropic-specific knobs (`anthropic_max_tokens`, `anthropic_timeout_seconds`) and default `llm_model` to `claude-opus-4-7` when anthropic is selected.

## Steps

### 1. Modify `src/app/config.py`
- Add fields under the `# -- LLM` block:
  - `anthropic_max_tokens: int = Field(default=4096, gt=0)`
  - `anthropic_timeout_seconds: int = Field(default=60, gt=0)`
  - `llm_model` stays optional but gains a validator default (step 2).
- Add a `@model_validator(mode="after")` named `_validate_llm_provider`:
  - If `self.llm_provider == "anthropic"`:
    - If `self.anthropic_api_key is None` → raise `ValueError("anthropic_api_key is required when llm_provider='anthropic'")`.
    - If `self.anthropic_api_key.get_secret_value().strip() == ""` → same error.
    - If `self.llm_model is None or self.llm_model.strip() == ""` → set `self.llm_model = "claude-opus-4-7"`.
  - If `self.llm_provider == "stub"`: no-op (leaves key optional, model `None`).
  - Return `self`.
- Import `Field` from `pydantic` if not already.

### 2. Modify `tests/test_config.py`
- Add an assertion to the existing "all fields present" check: the set must now include `anthropic_max_tokens` and `anthropic_timeout_seconds`.
- The deeper validation cases land in T-073's dedicated test file; keep this task's edit to the existing "field inventory" test only.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/config.py` | Modify | New fields + model_validator. |
| `tests/test_config.py` | Modify | Extend "all fields present" assertion. |

## Edge Cases & Risks
- A whitespace-only `ANTHROPIC_API_KEY` env var would pass a simple `is None` check but is effectively unconfigured. The validator's `.strip() == ""` branch handles that.
- The validator runs on `model_copy(update=...)` too — tests that build a Settings with `llm_provider="anthropic"` and then `model_copy(update={...})` must pass the key up-front. Document in the validator's docstring.
- Pydantic v2's `model_validator(mode="after")` receives `self` fully constructed; we CAN reassign `self.llm_model` because `Settings` is not frozen.

## Acceptance Verification
- [ ] `Settings(llm_provider="anthropic")` with no env key raises `pydantic.ValidationError`.
- [ ] `Settings(llm_provider="stub")` without any anthropic fields continues to succeed.
- [ ] Explicit `Settings(llm_provider="anthropic", anthropic_api_key="sk-ant-xxx")` resolves `llm_model == "claude-opus-4-7"`.
- [ ] `anthropic_max_tokens=0` → ValidationError via `gt=0`.
- [ ] `anthropic_timeout_seconds=-1` → ValidationError via `gt=0`.
- [ ] `uv run pyright` + `uv run ruff check .` clean.
- [ ] Full suite green (no regression — the deeper dedicated tests arrive in T-073).
