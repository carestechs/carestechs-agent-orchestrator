# Implementation Plan: T-064 — Adapter-thin check: quarantine the `anthropic` import

## Task Reference
- **Task ID:** T-064
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** None

## Overview
Extend the existing static-import guard so that `anthropic` can ONLY be imported from `src/app/core/llm.py` and `src/app/core/llm_anthropic.py`. Ships BEFORE the provider implementation so accidental leakage fails CI from the moment the import becomes possible.

## Steps

### 1. Modify `tests/test_adapters_are_thin.py`
- Add a new module-level constant:
  ```python
  _ANTHROPIC_ALLOWED = (
      _REPO_ROOT / "src" / "app" / "core" / "llm.py",
      _REPO_ROOT / "src" / "app" / "core" / "llm_anthropic.py",
  )
  ```
- Add helper `_walk_py_files(root: Path)` that yields every `.py` file under `src/app/` (excluding `src/app/migrations/`).
- Add a new test class `TestAnthropicImportQuarantine`:
  - `test_anthropic_only_imported_by_llm_seam(self)` — iterate every `.py` under `src/app/`, parse AST, collect any `import anthropic` / `from anthropic import …` nodes. Assert that every such node's file is in `_ANTHROPIC_ALLOWED`.
  - `test_walker_flags_injected_anthropic_import(self)` — sanity: parse `"import anthropic\n"` directly and confirm the helper would flag it.
- Keep the existing `TestThinAdapters` class unchanged.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/test_adapters_are_thin.py` | Modify | New quarantine class + walker. |

## Edge Cases & Risks
- `from anthropic.types import SomeType` inside an allowed file is fine (allow-list is by file, not by depth). The walker treats any `anthropic`-rooted module as the target.
- Migration files live at `src/app/migrations/versions/*.py` — keep them excluded so auto-generated revision diffs never trip this.
- Test files under `tests/` are NOT scanned — contract/live tests are allowed to import `anthropic` directly.

## Acceptance Verification
- [ ] Test class asserts zero `anthropic` imports outside the two allow-listed files.
- [ ] Sanity test confirms the walker would flag an offender.
- [ ] Existing `TestThinAdapters` continues to pass.
- [ ] `uv run pytest tests/test_adapters_are_thin.py` green.
