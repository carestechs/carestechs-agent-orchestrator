# Implementation Plan: T-051 — Service-layer unit tests

## Task Reference
- **Task ID:** T-051
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-040, T-041, T-042, T-043, T-044

## Overview
Per-service-function test files hitting the real DB fixture with a fake supervisor. Covers happy, pagination boundaries, filter combos, validation errors, not-found per function.

## Steps

### 1. Create `tests/modules/ai/conftest.py` additions (shared fakes)
- `class FakeSupervisor`: records `spawn`, `cancel`, `wake` calls; no-op bodies; `cancel()` no-ops if id not tracked.
- `class FakePolicy`: implements `LLMProvider` Protocol; scripted tool-call list; delegates to `StubLLMProvider`.
- `class FakeEngine`: implements `dispatch_node` as a stub returning a canned `engine_run_id` (tests that need engine failures override per-test).
- `class FakeTrace`: no-op `TraceStore`.
- Fixtures exposing each.

### 2. Create/extend `tests/modules/ai/test_service_start_run.py` (from T-040)
- Add: concurrent starts for distinct agents don't interfere; `start_run` returns different IDs each call.
- Add: `start_run` writes `trace_uri` with the correct path format.

### 3. Create/extend `tests/modules/ai/test_service_list_get.py` (from T-041)
- Already covered in T-041; verify page beyond total returns empty list + correct total.

### 4. Create/extend `tests/modules/ai/test_service_cancel.py` (from T-042)
- Already covered; add: cancel a pending run that has no supervised task (e.g., after zombie reconciliation re-init) — service should tolerate `supervisor.cancel` no-op.

### 5. Create/extend `tests/modules/ai/test_service_lists.py` (from T-043)
- Already covered in T-043.

### 6. Create/extend `tests/modules/ai/test_service_agents.py` (from T-044)
- Already covered in T-044.

### 7. Cross-cutting (`tests/modules/ai/test_service_contracts.py`)
- Confirm every `service.py` public function listed in `contracts/ai.py` → `IAIService` protocol is present with the declared signature (simple introspection).

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/modules/ai/conftest.py` | Create | Shared `Fake*` fixtures. |
| `tests/modules/ai/test_service_*.py` | Modify | Extend existing files with the added cases above. |
| `tests/modules/ai/test_service_contracts.py` | Create | IAIService protocol conformance. |

## Edge Cases & Risks
- Fakes must stay minimal — resist tempting "helpful" behavior drift that hides real service bugs.
- The `IAIService` protocol drift check fails loudly if a signature changes; that's the goal.

## Acceptance Verification
- [ ] Every public service function has ≥3 tests.
- [ ] Fakes are reusable across tests (kept in shared conftest).
- [ ] `IAIService` protocol test catches a deliberate signature change (smoke-check the test itself).
- [ ] `uv run pytest tests/modules/ai` green.
