# Implementation Plan: T-122 — Per-transition integration tests

## Task Reference
- **Task ID:** T-122
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** L
- **Dependencies:** T-115, T-116, T-117, T-118, T-119

## Overview
Comprehensive integration tests driving the real FastAPI app and real Postgres for every transition in the work-item and task state machines. Covers happy paths, illegal states, rejection loops (≥3 iter), defer from every non-terminal, derived-transition idempotency, and signal idempotency.

## Steps

### 1. Create `tests/integration/test_lifecycle_transitions.py`
- Fixtures (module-scoped):
  - `admin_headers` and `dev_headers` helpers that set `X-Actor-Role` + API key.
  - Factory fixtures: `make_work_item(status="open")`, `make_task(work_item, status="proposed", assignee_type=None)`.
- Test classes:
  - `TestWorkItemTransitions` — W1, W3, W4, W6 happy paths + their illegal-state cases (lock from `open`, unlock from `in_progress`, close from `in_progress`, etc.).
  - `TestDerivedTransitions` — W2 fires exactly once on first approval (second approval on same work item is idempotent); W5 fires exactly once when all tasks terminal; T4 fires inside approve.
  - `TestTaskTransitions` — one parametrized test per direct edge (T1-T11) + the deferral fan-out.
  - `TestRejectionLoops` — loop 3 × reject → re-submit → reject at `plan_review`; assert `Approval` count = 3 and task still in `planning`. Same shape for `impl_review`.
  - `TestDeferFanOut` — parametrize over all 7 non-terminal source states; defer each; assert `deferred_from` + W5 fires when last.
  - `TestIdempotency` — for each of the 14 signal endpoints: post twice with same body; assert second response has `meta.alreadyReceived=true`, no duplicate `Approval`/`TaskAssignment`/`TaskPlan`/`TaskImplementation`/state write.

### 2. Modify `tests/integration/conftest.py`
- Ensure session-scoped Postgres fixture is reused.
- Add the actor-header helpers if not already present.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/test_lifecycle_transitions.py` | Create | Comprehensive transition tests. |
| `tests/integration/conftest.py` | Modify | Actor-header fixtures. |

## Edge Cases & Risks
- **Test runtime** — 60 s budget. Use `asyncio.gather` where possible for parallel setup; parametrize aggressively.
- **Flaky derivations** — if W5 fires racily between tasks, tests can be non-deterministic. Use `await` chains strictly; no background tasks.
- **Postgres cleanup** — rely on the existing `_cleanup_rows` fixture (updated in T-107..T-110). Each test should seed fresh rows.
- **Fixture reuse with T-125** — extract factory fixtures to `tests/integration/fixtures/lifecycle.py` so T-125's E2E test can reuse them.

## Acceptance Verification
- [ ] One test per explicit transition.
- [ ] Derived transitions fire exactly once (W2, W5, T4).
- [ ] Rejection loops ≥3 iter work without corruption.
- [ ] Defer from every non-terminal state passes.
- [ ] Idempotency tests cover all 14 signals.
- [ ] Full suite runs in <60 s.
- [ ] `uv run pytest tests/integration/test_lifecycle_transitions.py` green.
