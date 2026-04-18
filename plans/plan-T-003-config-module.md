# Implementation Plan: T-003 — Config module (`app.config`)

## Task Reference
- **Task ID:** T-003
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** Required by every other component that needs config (DB URL, API key, webhook secret, engine, LLM). Validates at startup per the profile.

## Overview
Implement a single `Settings` class using `pydantic-settings` that reads env vars and `[tool.orchestrator]` from `pyproject.toml` in the precedence order documented in `ui-specification.md` → Configuration Sources. Expose `get_settings()` memoized via `lru_cache`, overridable in tests.

## Implementation Steps

### Step 1: Define `Settings`
**File:** `src/app/config.py`
**Action:** Modify

Replace the placeholder module with a `Settings(BaseSettings)` class. Fields (all snake_case Python, env-var mapping via the default upper-case behavior plus explicit aliases where needed):

- `database_url: PostgresDsn` (required, env `DATABASE_URL`).
- `orchestrator_api_key: SecretStr` (required, env `ORCHESTRATOR_API_KEY`).
- `engine_webhook_secret: SecretStr` (required, env `ENGINE_WEBHOOK_SECRET`).
- `engine_base_url: AnyHttpUrl` (required, env `ENGINE_BASE_URL`).
- `engine_api_key: SecretStr | None` (optional).
- `llm_provider: Literal["stub", "anthropic"]` (default `"stub"`).
- `llm_model: str | None` (optional).
- `anthropic_api_key: SecretStr | None` (optional).
- `agents_dir: Path` (default `Path("agents")`).
- `log_level: Literal["DEBUG","INFO","WARNING","ERROR"]` (default `"INFO"`).

`model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)`.

### Step 2: Add the pyproject.toml settings source
**File:** `src/app/config.py`
**Action:** Modify

Implement a custom `PyprojectTomlSource(PydanticBaseSettingsSource)` that reads `[tool.orchestrator]` from the nearest `pyproject.toml` walking up from CWD. Inject it into the settings sources via `@classmethod settings_customise_sources`, placing it after env vars and before defaults (per the precedence in `ui-specification.md`).

### Step 3: Implement `get_settings()`
**File:** `src/app/config.py`
**Action:** Modify

```python
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

Validation errors from `Settings()` construction propagate — callers get a fast-fail at import time. No try/except.

### Step 4: Add FastAPI dependency shim
**File:** `src/app/core/dependencies.py`
**Action:** Modify

Add `def get_settings_dep() -> Settings: return get_settings()` as a FastAPI dependency. Routes use `Depends(get_settings_dep)`; tests override via `app.dependency_overrides[get_settings_dep] = lambda: Settings(...)`. Never call `get_settings()` directly from a route handler — go through the dep so tests can override.

### Step 5: Tests
**File:** `tests/test_config.py`
**Action:** Create

- **Env-var happy path:** monkeypatch all required env vars, assert `Settings()` loads without error and values match.
- **Missing required field:** unset one required var, assert `ValidationError` raised at construction and the error message names the field.
- **Pyproject layer:** write a temp `pyproject.toml` with `[tool.orchestrator] log_level = "DEBUG"`, `chdir` into it, clear `lru_cache`, assert that layer overrides the default but NOT env.
- **Precedence sanity:** set env `LOG_LEVEL=ERROR` AND pyproject `log_level = "DEBUG"`, assert result is `ERROR`.

## Files Affected

| File | Action | Summary |
|------|--------|---------|
| `src/app/config.py` | Modify | `Settings` + `get_settings()` + pyproject source |
| `src/app/core/dependencies.py` | Modify | Add `get_settings_dep` FastAPI dependency |
| `tests/test_config.py` | Create | Precedence + missing-field tests |

## Edge Cases & Risks

- **`lru_cache` vs test isolation.** Tests that change env vars must call `get_settings.cache_clear()` in setup/teardown or the first test's result sticks. Add a pytest fixture `clear_settings_cache` that auto-uses in `tests/test_config.py`.
- **`PostgresDsn` and `AnyHttpUrl` validation.** Pydantic v2 is strict about schemes and hosts. `localhost` works for `AnyHttpUrl`; the `postgresql+asyncpg://` scheme is what SQLAlchemy expects — verify `PostgresDsn` accepts it, else use `str` with a field validator.
- **`SecretStr` serialization.** When logging, `SecretStr` renders as `**********` — good. But `.env.example` must document the plain env-var name, not `SecretStr`, so users don't get confused.
- **Pyproject source path lookup.** Walking up from CWD finds the wrong file if the CLI is invoked from an odd directory (e.g., `/tmp`). Lookup must stop at filesystem root and silently produce an empty layer if no `pyproject.toml` is found.

## Acceptance Verification

- [ ] **AC (fields present):** `Settings.model_fields` includes every field listed in the task + Step 1.
- [ ] **AC (precedence):** tests for env > pyproject > default all pass.
- [ ] **AC (fail-fast):** importing `app.main` with one required field unset raises `ValidationError` that names that field (verify with `pytest -k missing_field`).
- [ ] **AC (testable):** `app.dependency_overrides[get_settings_dep]` replaces the instance; verified in a fixture-style test.
