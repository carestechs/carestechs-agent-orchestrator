# Implementation Plan: T-163 — Request-assignment effector (log-only transport)

## Task Reference
- **Task ID:** T-163
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Rationale:** AC-5. Proves the effector seam for a new capability, not just relocated code. Closes the most obvious missing integration — today `task:assigning` is silent.

## Overview
On `task: * → assigning` (T4 entry, triggered by task-approve), fire a `RequestAssignmentEffector` that emits a structured log + trace entry: "task T-001 needs an assignee." No external transport in v1 — the effector contract is pluggable, so Slack/email/webhook transports land as separate effectors later.

## Implementation Steps

### Step 1: Author the effector
**File:** `src/app/modules/ai/lifecycle/effectors/assignment.py`
**Action:** Create

```python
from __future__ import annotations
import logging
import time
from typing import ClassVar
from sqlalchemy import select
from app.modules.ai.lifecycle.effectors.base import Effector
from app.modules.ai.lifecycle.effectors.context import (
    EffectorContext, EffectorResult,
)
from app.modules.ai.models import Task, WorkItem

logger = logging.getLogger(__name__)


class RequestAssignmentEffector:
    name: ClassVar[str] = "request_assignment"

    async def fire(self, ctx: EffectorContext) -> EffectorResult:
        start = time.monotonic()
        task = await ctx.db.scalar(
            select(Task).where(Task.id == ctx.entity_id)
        )
        if task is None:
            return EffectorResult(
                effector_name=self.name,
                status="error",
                duration_ms=int((time.monotonic() - start) * 1000),
                error_code="task-not-found",
            )
        wi = await ctx.db.scalar(
            select(WorkItem).where(WorkItem.id == task.work_item_id)
        )
        logger.info(
            "task needs assignee",
            extra={
                "task_id": str(task.id),
                "task_ref": task.external_ref,
                "work_item_id": str(task.work_item_id),
                "work_item_ref": wi.external_ref if wi else None,
                "title": task.title,
            },
        )
        return EffectorResult(
            effector_name=self.name,
            status="ok",
            duration_ms=int((time.monotonic() - start) * 1000),
            metadata={
                "task_ref": task.external_ref,
                "work_item_ref": wi.external_ref if wi else None,
            },
        )
```

### Step 2: Register
**File:** `src/app/modules/ai/lifecycle/effectors/bootstrap.py`
**Action:** Modify

```python
registry.register(
    "task:entry:assigning",
    RequestAssignmentEffector(),
)
```

Key choice: `entry:assigning` (state-entry, from-state-agnostic) rather than a specific `proposed->assigning` key. The task enters `assigning` exactly once in the happy path; if there's ever a looping path that re-enters `assigning`, the same notification is probably correct.

### Step 3: Unit test
**File:** `tests/modules/ai/lifecycle/effectors/test_assignment.py`
**Action:** Create

Cases:
- **Happy path.** Seed a task + work item, construct an `EffectorContext` pointing at the task, fire the effector, assert result.status == "ok", result.metadata contains both refs, and a log record was emitted with the expected `extra` fields (use `caplog` with direct-handler pattern from FEAT-007's test setup to avoid propagation issues).
- **Missing task.** Fire against a random uuid → `status="error"`, `error_code="task-not-found"`.
- **Work item missing (orphan task).** Task exists but work item doesn't → effector still logs with `work_item_ref=None`, returns `ok`. Defensive, not an error.

### Step 4: Ensure registration is exercised
**File:** `tests/modules/ai/lifecycle/effectors/test_bootstrap.py`
**Action:** Create (if not already by T-162)

Simple coverage test: after `register_all_effectors(registry, settings, github)`, asserting `"task:entry:assigning"` maps to at least one effector and that effector's name is `"request_assignment"`. Catches accidental deregistration in future PRs.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/lifecycle/effectors/assignment.py` | Create | Effector. |
| `src/app/modules/ai/lifecycle/effectors/bootstrap.py` | Modify | Register. |
| `tests/modules/ai/lifecycle/effectors/test_assignment.py` | Create | Unit tests. |
| `tests/modules/ai/lifecycle/effectors/test_bootstrap.py` | Create or Modify | Registration coverage. |

## Edge Cases & Risks
- **Log record capture interference.** FEAT-007's `test_noop_client_returns_sentinel` ran into pytest-caplog propagation issues when other tests configured the root logger. Use the direct-handler pattern already proven there (see `tests/modules/ai/github/test_checks.py::test_noop_client_returns_sentinel`) rather than `caplog.at_level`.
- **Transport silence.** V1 does nothing visible to the assignee. The log line is for the operator to monitor via `journalctl`/JSON stack. Make sure the log fields (task_ref, work_item_ref) are grep-friendly.
- **Effector context carries a DB session.** The effector opens one query per fire. Fine at signal throughput (dozens/day); if throughput grows 100×, push this to a read-through cache. Not in v1 scope.

## Acceptance Verification
- [ ] Effector registered on `task:entry:assigning`.
- [ ] Fires exactly one log record with `task_ref` and `work_item_ref`.
- [ ] Trace entry written as a normal `effector_call`.
- [ ] Unit tests cover happy path + missing task + missing work item.
- [ ] `uv run pyright`, `ruff`, `pytest tests/modules/ai/lifecycle/effectors/test_assignment.py` green.
