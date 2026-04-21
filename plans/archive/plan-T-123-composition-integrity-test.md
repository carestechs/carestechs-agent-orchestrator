# Implementation Plan: T-123 — Composition-integrity test (AD-3)

## Task Reference
- **Task ID:** T-123
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-122

## Overview
Single end-to-end test proving FEAT-006 runs fully with `LLM_PROVIDER=stub` and no GitHub configuration. Regression guard for "did we accidentally put logic in the engine or make GitHub mandatory."

## Steps

### 1. Create `tests/integration/test_composition_integrity_feat006.py`
- Fixture configures the app with:
  - `LLM_PROVIDER=stub`
  - `GITHUB_WEBHOOK_SECRET=None`, `GITHUB_APP_ID=None`, `GITHUB_PAT=None`
  - `get_github_checks_client_dep` overridden to return `NoopGitHubChecksClient()` (should be the default, but explicit override guards against future config drift).
- Single long-form test `test_feat006_composition_integrity`:
  1. Open work item (S1) — admin.
  2. Seed 2 tasks directly via service (skipping agent task-generation).
  3. Approve task A (S5) — fires W2 + T4.
  4. Assign A to dev (S7), assign B to agent (S7).
  5. Submit plan A (S8, dev), approve plan A (S9, dev).
  6. Submit plan B (S8, admin proxy), approve plan B (S9, admin).
  7. Submit implementation A (S11 via `/implementation`), approve review A (S12 admin).
  8. Submit implementation B (S11), reject review B (S13) with feedback, re-submit, approve (S12).
  9. W5 fires — work item auto-advances to `ready`.
  10. Close work item (S4).
- Assertions:
  - Final state: work item `closed`, both tasks `done`, 3+ `Approval` rows including one reject.
  - No outbound GitHub API calls (verify via the noop client's internal call counter, or by injecting a recording double that asserts zero calls).
  - Engine client (if any) received zero transition-logic calls.

### 2. Modify `tests/integration/conftest.py` (if needed)
- Helper to build an app with env-var overrides, reusing the existing test app factory.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_composition_integrity_feat006.py` | Create | Single long-form composition test. |
| `tests/integration/conftest.py` | Modify | Env-override app factory if missing. |

## Edge Cases & Risks
- **Flakiness from shared app state** — use a fresh app instance per test run; don't share the global FastAPI app with other test modules.
- **Parametrization temptation** — resist. This test is a single regression scenario; failure must point at a specific step.
- **Stub LLM determinism** — verify `StubLLMProvider` returns predictable sequences; if FEAT-006 eventually invokes it for task-generation, the stub must cover that path.

## Acceptance Verification
- [ ] Test runs with stub LLM + no GitHub config.
- [ ] All 14 signal types exercised.
- [ ] Work item + tasks reach expected terminal states.
- [ ] Zero GitHub API calls verified.
- [ ] Runs in <30 s.
- [ ] `uv run pytest tests/integration/test_composition_integrity_feat006.py` green.
