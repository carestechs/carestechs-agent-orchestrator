# Implementation Plan: T-087 — Extend `AgentDefinition` with `policy.system_prompts`

## Task Reference
- **Task ID:** T-087
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** None

## Overview
Add an optional `policy` block to `AgentDefinition` whose only field in v1 is `system_prompts: dict[node_name, Path]`. The loader validates (at YAML-parse time) that every key is a declared node and every path exists under the repo root. Prompt *contents* are not read here — T-100's runtime prompt-assembly code reads them at run start.

## Steps

### 1. Modify `src/app/modules/ai/agents.py`
- Add a new `AgentPolicy` Pydantic model below `AgentFlow`:
  ```python
  class AgentPolicy(BaseModel):
      """LLM-facing policy configuration (v1: just per-node system prompts)."""

      model_config = _CAMEL_CONFIG

      system_prompts: dict[str, Path] = Field(default_factory=dict)
  ```
- Add the field on `AgentDefinition`:
  ```python
  policy: AgentPolicy = Field(default_factory=AgentPolicy)
  ```
- Extend `_check_invariants` to validate `policy.system_prompts`:
  - Unknown node keys: `raise ValueError(f"system_prompts references unknown nodes: {sorted(unknown)}")`.
  - Missing files / path-escapes: delegate to a new helper `_validate_prompt_path(path, repo_root)` that calls `path.resolve(strict=True)` and asserts `repo_root in resolved.parents or resolved == repo_root / ...`.
- Resolve `repo_root` via `app.config.get_settings().repo_root` imported at module top. Keep the import guarded by `TYPE_CHECKING` if it creates a cycle; fall back to `Path.cwd()` only inside the validator body.

### 2. Modify `tests/modules/ai/test_agents.py`
- Add four test methods:
  - `test_agent_policy_defaults_empty` — `AgentDefinition` constructed without `policy` validates; `a.policy.system_prompts == {}`.
  - `test_system_prompts_rejects_unknown_node` — key `"bogus"` not in `nodes` → `ValidationError`.
  - `test_system_prompts_rejects_missing_file` — path `prompts/does-not-exist.md` → `ValidationError` mentioning "prompt file not found".
  - `test_system_prompts_rejects_escape_root` — path `../../etc/passwd` → `ValidationError` mentioning "escape".
- Use `tmp_path` to create a real prompt file for the happy-path case.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/agents.py` | Modify | New `AgentPolicy` model, new field on `AgentDefinition`, validator extension. |
| `tests/modules/ai/test_agents.py` | Modify | Four new test cases. |

## Edge Cases & Risks
- **Cycle risk on `get_settings()`**: `agents.py` is imported early. Lazy-resolve via `Path.cwd()` if `get_settings()` raises `RuntimeError` during validation.
- **Windows path handling**: `Path.resolve()` normalizes slashes; asserts use `Path` comparisons, not string equality.
- **Hash determinism (T-100's concern)**: canonical JSON sorts keys, so `system_prompts` doesn't break `agent_definition_hash` — but verify in T-100's hash test.
- **Backwards compat**: existing agent fixtures in `tests/fixtures/` that omit `policy` MUST still load. `Field(default_factory=AgentPolicy)` handles this; grep fixtures to confirm none already declare a `policy` key with a different shape.

## Acceptance Verification
- [ ] `AgentPolicy` defined with `system_prompts: dict[str, Path]`; `AgentDefinition.policy` defaults to empty.
- [ ] Validator rejects unknown node keys, missing files, escape-root paths.
- [ ] All four new tests pass.
- [ ] Existing FEAT-002 agent-loader tests remain green (run `uv run pytest tests/modules/ai/test_agents.py`).
- [ ] `uv run pyright` and `uv run ruff check .` clean.
