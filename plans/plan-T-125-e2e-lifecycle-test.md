# Implementation Plan: T-125 — Full E2E lifecycle test (AC-10)

## Task Reference
- **Task ID:** T-125
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Dependencies:** T-122, T-123, T-124

## Overview
Single long-form end-to-end test implementing the AC-10 scenario: open → 2 tasks (one approved, one rejected+re-approved) → assign (dev + agent) → plan/review each → implement/review each → done → ready → closed. All 14 signals exercised via the HTTP API.

## Steps

### 1. Create `tests/integration/test_feat006_e2e.py`
- Reuse actor-header helpers and factory fixtures from T-122 (refactored into `tests/integration/fixtures/lifecycle.py` if needed).
- Single function `test_feat006_full_lifecycle`:
  1. S1 admin opens work item `FEAT-900` (test-only id).
  2. Seed task `T-900-A` and `T-900-B` via direct service calls (task-generation stub doesn't produce content in v1).
  3. S5 approve `T-900-A` (admin) — W2 fires.
  4. S6 reject `T-900-B` (admin) with feedback "please split".
  5. Proposer re-submits (direct service call since re-propose endpoint is not in scope).
  6. S5 approve `T-900-B`.
  7. S7 assign A to dev, B to agent.
  8. S8 submit plan A (dev), S9 approve plan A (dev).
  9. S8 submit plan B (admin proxy), S9 approve plan B (admin).
  10. S11 submit implementation A (admin proxy), S12 approve review A (admin).
  11. S11 submit implementation B, S13 reject review B with feedback.
  12. S11 re-submit, S12 approve review B.
  13. W5 fires — work item `ready`.
  14. S4 close work item.
  15. Insert S2/S3 (lock/unlock) somewhere mid-flow to exercise those signals.
  16. Insert S14 (defer) on a throw-away third task to cover it.
- Assertions:
  - Work item `closed`; both tasks `done`.
  - `Approval` row count matches expected (at least: 2 proposed-approve, 1 proposed-reject, 2 plan-approve, 2 impl-approve, 1 impl-reject).
  - `TaskAssignment` count: 2 (one per task; no reassignments).
  - `LifecycleSignal` count ≥ 14 (at least one per signal type).

### 2. Optionally extract shared fixtures
- `tests/integration/fixtures/lifecycle.py` — factory helpers reused by T-122, T-123, T-125.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_feat006_e2e.py` | Create | E2E scenario. |
| `tests/integration/fixtures/lifecycle.py` | Create/Modify | Shared factories. |

## Edge Cases & Risks
- **S2/S3 in the middle of the flow** — locking between approving A and assigning B is fine; unlocking returns to `in_progress`. Must not break W5.
- **Throw-away third task for S14** — add it after step 3 so it's in `proposed`; defer it as one of the last actions.
- **Test runtime** — 45 s budget; keep DB operations tight.
- **Trace audit count** — if the trace stream is queryable, assert exactly N entries per signal. If not, assert on DB-level audit queries.

## Acceptance Verification
- [ ] All 14 signal types exercised.
- [ ] Final state: work item `closed`, both tasks `done`.
- [ ] Expected `Approval` / `TaskAssignment` / `LifecycleSignal` counts match.
- [ ] Runs in <45 s.
- [ ] `uv run pytest tests/integration/test_feat006_e2e.py` green.
