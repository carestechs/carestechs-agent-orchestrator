# Implementation Plan: T-166 — `await_reactor` test helper + migrate existing tests

## Task Reference
- **Task ID:** T-166
- **Type:** Testing
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** AC-12. T-167 breaks every synchronous aux-row assertion. The helper must land first so T-167 has something to lean on.

## Overview
A single shared helper `await_reactor(session, predicate, timeout=5s)` that polls until `predicate(session)` returns truthy, raises with a diagnostic dump on timeout. Migrate all FEAT-006 + FEAT-007 tests that currently read aux rows synchronously after a 202.

## Implementation Steps

### Step 1: Implement the helper
**File:** `tests/integration/_reactor_helpers.py`
**Action:** Create

```python
from __future__ import annotations
import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")

_DEFAULT_TIMEOUT_SECONDS = 5.0
_DEFAULT_INTERVAL_SECONDS = 0.05


class ReactorWaitTimeout(AssertionError):
    """Raised when ``await_reactor`` exhausts its budget without a match."""


async def await_reactor(
    session: AsyncSession,
    predicate: Callable[[AsyncSession], Awaitable[T]],
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    interval: float = _DEFAULT_INTERVAL_SECONDS,
    description: str = "reactor predicate",
) -> T:
    """Poll ``predicate`` until it returns truthy or *timeout* elapses.

    The predicate is async and receives the session directly — lets
    callers write complex queries inline.  On timeout, raises with the
    last result (typically ``None`` or empty list) so failure diagnostics
    are self-describing.

    Expire-all-refresh pattern: callers that hold stale ORM instances
    should call ``session.expire_all()`` before their assertions.  The
    helper itself doesn't expire — the predicate's query is authoritative.
    """
    deadline = time.monotonic() + timeout
    last_result: T | None = None
    while time.monotonic() < deadline:
        last_result = await predicate(session)
        if last_result:
            return last_result
        await asyncio.sleep(interval)
    raise ReactorWaitTimeout(
        f"{description} did not become truthy within {timeout}s; "
        f"last result: {last_result!r}"
    )
```

Key design calls:
- **Predicate receives the session.** Callers write inline queries. No lifting to dataclasses.
- **Truthy match.** Returning a non-empty list, a non-None row, a non-zero count all count. Matches intuition.
- **Descriptive error.** On timeout, the message + last result let a test reviewer see what was *almost* there.

### Step 2: Convenience wrappers for common cases
**File:** `tests/integration/_reactor_helpers.py`
**Action:** Modify

Add targeted wrappers to avoid verbose predicate lambdas in every test:

```python
async def await_task_status(
    session: AsyncSession, task_id: uuid.UUID, expected: str, **kwargs
) -> Task:
    async def predicate(s: AsyncSession) -> Task | None:
        s.expire_all()
        task = await s.scalar(select(Task).where(Task.id == task_id))
        return task if task and task.status == expected else None
    return await await_reactor(
        session, predicate, description=f"task {task_id} → {expected}", **kwargs
    )


async def await_aux_row_count(
    session: AsyncSession, model: type, task_id: uuid.UUID, minimum: int = 1,
    **kwargs,
) -> int:
    async def predicate(s: AsyncSession) -> int:
        count = await s.scalar(
            select(func.count(model.id)).where(model.task_id == task_id)
        )
        return count if count and count >= minimum else 0
    return await await_reactor(
        session, predicate,
        description=f"{model.__name__} count >= {minimum} for task {task_id}",
        **kwargs,
    )
```

### Step 3: Migrate FEAT-006 e2e assertions
**File:** `tests/integration/test_feat006_e2e.py`
**Action:** Modify

Every `assert wi.status == ...` or aux-row count assertion that follows a signal POST gets wrapped. Pattern:

```python
# Before
await client.post(f"/api/v1/tasks/{task.id}/approve", ...)
await session.refresh(task)
assert task.status == TaskStatus.ASSIGNING.value

# After
await client.post(f"/api/v1/tasks/{task.id}/approve", ...)
await await_task_status(session, task.id, TaskStatus.ASSIGNING.value)
```

**Important:** the rewrite only affects post-signal assertions. Seed + read paths stay direct. Don't over-wrap.

### Step 4: Migrate FEAT-007 assertions
**File:** `tests/integration/test_feat007_merge_gating.py`, `test_feat007_github_integration.py`, `test_feat007_composition_integrity.py`, `test_feat005_feat006_coexistence.py`
**Action:** Modify

Same pattern. Most FEAT-007 tests assert on the respx call side, not the DB side, so the migration footprint is smaller than FEAT-006. The DB-side assertions that exist (looking at `TaskImplementation.github_check_id`) need the wrapper.

### Step 5: Convert the "run to completion" unit tests
**File:** `tests/modules/ai/test_router_tasks_plan_review.py`
**Action:** Modify (if needed)

These tests drive the signal adapters directly at the service layer. They might not need the helper — if the adapter still owns the commit boundary, the aux rows are there synchronously. But after T-167, the adapter no longer owns that boundary. Check each test: if it asserts on aux rows, wrap.

### Step 6: Self-test the helper
**File:** `tests/integration/test_reactor_helpers.py`
**Action:** Create

Narrow tests:
- **Match on first poll.** Predicate returns truthy immediately → no sleep, result returned.
- **Match after a few polls.** Predicate returns falsy for 100ms, truthy after → result returned, total wait < 500ms.
- **Timeout.** Predicate always returns falsy → `ReactorWaitTimeout` raised with the descriptive message.
- **Non-truthy types.** Empty list, `None`, `0` all treated as "not yet" — move to next poll.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `tests/integration/_reactor_helpers.py` | Create | Helper + convenience wrappers. |
| `tests/integration/test_reactor_helpers.py` | Create | Helper self-tests. |
| `tests/integration/test_feat006_e2e.py` | Modify | Wrap post-signal assertions. |
| `tests/integration/test_feat007_merge_gating.py` | Modify | Wrap aux-row assertions. |
| `tests/integration/test_feat007_github_integration.py` | Modify | Same. |
| `tests/integration/test_feat007_composition_integrity.py` | Modify | Same. |
| `tests/integration/test_feat005_feat006_coexistence.py` | Modify | Same. |
| `tests/modules/ai/test_router_tasks_plan_review.py` | Modify (conditional) | Wrap if service-level assertion still reads aux rows. |

## Edge Cases & Risks
- **SAVEPOINT session and `expire_all`.** FEAT-007 hit a greenlet error when `expire_all` triggered lazy load from inside an async context. The helper avoids this by having the predicate run its own fresh query each poll — the session's identity map isn't relied on. Keep it that way.
- **False negatives from bad assertions.** If a test writes a predicate that can never become truthy, the 5s wait is wasted every run. Reviewers should push back on any predicate that returns `None` in the happy path. Good descriptions in the raise help.
- **CI flakiness.** 5s is generous for local runs but might be tight on slow CI runners. Tune once we see the test suite on the real CI and adjust the default. Start at 5s.
- **Tests that currently pass synchronously will still pass with the helper** — predicate matches on the first poll. No correctness regression.

## Acceptance Verification
- [ ] Helper + convenience wrappers exist.
- [ ] Self-tests cover happy path, delayed match, timeout, non-truthy types.
- [ ] Every post-signal aux-row / status assertion in `tests/integration/test_feat006_*` wrapped.
- [ ] Same for `test_feat007_*`.
- [ ] Full integration suite still green (wrappers no-op until T-167 changes the timing model).
- [ ] `uv run pyright`, `ruff`, `pytest tests/integration/` green.
