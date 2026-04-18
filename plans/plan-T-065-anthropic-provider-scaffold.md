# Implementation Plan: T-065 — Add `anthropic` to runtime deps + `AnthropicLLMProvider` skeleton

## Task Reference
- **Task ID:** T-065
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-063, T-064

## Overview
Move `anthropic` into the required runtime dependencies and create the `AnthropicLLMProvider` module with a minimal class shape — async client wired from `Settings`, `chat_with_tools` a placeholder. Sets up the scaffold so T-066/T-067/T-068 layer in behavior without re-touching the module boundary.

## Steps

### 1. Modify `pyproject.toml`
- Append `"anthropic>=0.40,<1"` to the `[project].dependencies` list (alphabetically after `aiofiles`).
- Remove the `[project.optional-dependencies].anthropic = ["anthropic>=0.40,<1"]` stanza.
- Run `uv lock` so `uv.lock` regenerates with the pinned version in the main manifest.

### 2. Create `src/app/core/llm_anthropic.py`
- Imports: `import anthropic`, `from typing import Any, Mapping, Sequence`, `from app.config import Settings`, `from app.core.exceptions import NotImplementedYet`, `from app.core.llm import LLMProvider, ToolCall, ToolDefinition`.
- Class `AnthropicLLMProvider`:
  - Class attributes `name: str = "anthropic"` and `model: str` (set in `__init__`).
  - `def __init__(self, settings: Settings) -> None:`
    - `self.model = settings.llm_model or "claude-opus-4-7"` (defensive — the settings validator from T-063 should have set it, but don't rely on that here).
    - `self._max_tokens = settings.anthropic_max_tokens`.
    - `self._timeout = settings.anthropic_timeout_seconds`.
    - `assert settings.anthropic_api_key is not None, "settings validator should have ensured this"`.
    - `self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value(), timeout=float(self._timeout))`.
  - `async def chat_with_tools(self, *, system: str, messages: Sequence[Mapping[str, Any]], tools: Sequence[ToolDefinition]) -> ToolCall:`
    - Body: `raise NotImplementedYet("anthropic chat_with_tools")` — real body lands in T-066.
  - Do NOT implement `__aenter__/__aexit__` or `aclose` in v1; the SDK's client is safe to leave open for the process lifetime.

### 3. Create `tests/modules/core/__init__.py` (if missing)
- Empty file so the new test module is collected.

### 4. Create `tests/modules/core/test_llm_anthropic_construction.py`
- `def test_constructor_does_not_hit_the_network` — build `Settings(llm_provider="anthropic", anthropic_api_key="sk-ant-xxx", ...)` (pass all required fields explicitly), instantiate `AnthropicLLMProvider(settings)`, assert `isinstance(provider._client, anthropic.AsyncAnthropic)`, and that no respx mocks were hit (i.e., using `respx.mock(base_url="https://api.anthropic.com")` and asserting `len(route.calls) == 0` at the end).
- `def test_protocol_match` — assert `isinstance(provider, LLMProvider)` (runtime-checkable protocol).
- `def test_model_defaults_to_claude_opus` — when `llm_model` is None in settings, the provider's `.model` is `"claude-opus-4-7"` (via the settings validator).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `pyproject.toml` | Modify | `anthropic` promoted to runtime dep. |
| `uv.lock` | Modify | Regenerated. |
| `src/app/core/llm_anthropic.py` | Create | Provider scaffold. |
| `tests/modules/core/__init__.py` | Create (if missing) | Package marker. |
| `tests/modules/core/test_llm_anthropic_construction.py` | Create | Construction + protocol tests. |

## Edge Cases & Risks
- `tests/` may not yet have a `modules/core/` subdirectory — check and create the `__init__.py` if needed.
- `anthropic.AsyncAnthropic(api_key="sk-ant-xxx")` does NOT make a network call at construction time; verify by running the test without any respx route and confirming it passes.
- The SDK may emit a warning when a suspiciously-short/fake key is used. That's fine for tests; don't suppress it.

## Acceptance Verification
- [ ] `anthropic` appears under `[project].dependencies` and is gone from `[project.optional-dependencies]`.
- [ ] `uv sync` succeeds and `uv.lock` updated.
- [ ] `isinstance(provider, LLMProvider)` is True.
- [ ] Constructor does not touch the network (tested via respx).
- [ ] Thin-adapter check (T-064) still passes — `anthropic` imported only in the allowed file.
- [ ] `uv run pyright` + `uv run ruff check .` clean.
