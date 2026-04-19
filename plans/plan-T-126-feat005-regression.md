# Implementation Plan: T-126 — FEAT-005 regression test (AC-11)

## Task Reference
- **Task ID:** T-126
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-125

## Overview
Prove FEAT-005's lifecycle agent still works unchanged after FEAT-006 lands. Adds a single coexistence test that runs both lifecycle paths back-to-back in the same process.

## Steps

### 1. Audit existing FEAT-005 tests
- Run `uv run pytest tests/integration/ -k "lifecycle"` and verify all FEAT-005 integration tests pass unchanged.
- If any fail, investigate — the regression must be fixed before FEAT-006 can ship.

### 2. Create `tests/integration/test_lifecycle_coexistence.py`
- Single test:
  - Start a FEAT-005 run against a small IMP work item (reuse fixtures from existing FEAT-005 suite — e.g., `IMP-999` stub). Use `LLM_PROVIDER=stub`.
  - Assert the run completes with `stop_reason=done_node`.
  - Immediately: drive a FEAT-006 work-item + task flow (abbreviated — open, approve one task, defer it, close via admin) in the same test process.
  - Assert both paths reach their terminal states without cross-contamination (no shared state leaking between lifecycles).

### 3. Do NOT modify any FEAT-005 test or production code
- If this test needs changes to FEAT-005 to pass, something in FEAT-006 broke it — fix the regression in the FEAT-006 code, not in FEAT-005.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_lifecycle_coexistence.py` | Create | Coexistence test. |

## Edge Cases & Risks
- **Shared supervisor state** — FEAT-005's `RunSupervisor` and FEAT-006's work-item logic both live in the same process. If FEAT-005's supervisor leaks state between runs, the second lifecycle path might inherit it. Spin up a fresh app per lifecycle inside the test if needed.
- **Database conflicts** — both paths write to `runs`, `steps`, etc. (FEAT-005) and to the new FEAT-006 tables. Ensure `_cleanup_rows` covers both sets.
- **Env-var pollution** — if FEAT-005 depends on specific config that FEAT-006 overrides, restore between runs.

## Acceptance Verification
- [ ] All FEAT-005 integration tests pass unchanged.
- [ ] Coexistence test spins up both lifecycles and both complete.
- [ ] Zero modifications to FEAT-005 production code or tests.
- [ ] `uv run pytest tests/integration/` fully green.
