# Implementation Plan: T-164 — Task-generation effector (inline deterministic)

## Task Reference
- **Task ID:** T-164
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** M
- **Rationale:** AC-6. Unblocks the full flow — today you seed tasks by hand because `dispatch_task_generation` is a stub. Deterministic so it can be tested without an LLM. Seam is the same one an LLM-backed generator will plug into later.

## Overview
Replace the log-only stub in `lifecycle/service.py::dispatch_task_generation` with a `GenerateTasksEffector` registered on `work_item:entry:pending_tasks` (W1). The v1 generator is deterministic: reads the work item's `type` and `title` and emits 1-3 seed tasks per a fixed scaffold. No LLM, no brief-markdown parsing.

## Implementation Steps

### Step 1: Design the scaffold
**File:** `src/app/modules/ai/lifecycle/effectors/task_generation.py`
**Action:** Create

Scaffold by `WorkItemType`:

| Work item type | Seed tasks |
|----------------|-----------|
| `FEAT` | 1. "Investigate + plan" · 2. "Implement" · 3. "Review + close" |
| `BUG` | 1. "Reproduce + root cause" · 2. "Fix" · 3. "Verify + close" |
| `IMP` | 1. "Scope + plan" · 2. "Apply improvement" |

Each seed task carries: `title`, `external_ref` (computed from the work-item's ref + a sequence suffix, e.g. `T-FEAT-042-01`), `status=proposed`, `proposer_type="system"`, `proposer_id="task_generation"`.

These scaffolds are *placeholders* — the real value arrives when an LLM-backed generator replaces this. Goal for v1 is "the flow doesn't stall at pending_tasks."

### Step 2: Implement the effector
**File:** `src/app/modules/ai/lifecycle/effectors/task_generation.py`
**Action:** Modify

```python
from __future__ import annotations
import logging
import time
import uuid
from typing import ClassVar
from sqlalchemy import select, func
from app.modules.ai.enums import WorkItemType, TaskStatus
from app.modules.ai.lifecycle.effectors.base import Effector
from app.modules.ai.lifecycle.effectors.context import (
    EffectorContext, EffectorResult,
)
from app.modules.ai.models import Task, WorkItem

logger = logging.getLogger(__name__)


_SCAFFOLDS: dict[str, list[str]] = {
    WorkItemType.FEAT.value: ["Investigate + plan", "Implement", "Review + close"],
    WorkItemType.BUG.value: ["Reproduce + root cause", "Fix", "Verify + close"],
    WorkItemType.IMP.value: ["Scope + plan", "Apply improvement"],
}


class GenerateTasksEffector:
    name: ClassVar[str] = "generate_tasks"

    async def fire(self, ctx: EffectorContext) -> EffectorResult:
        start = time.monotonic()
        wi = await ctx.db.scalar(
            select(WorkItem).where(WorkItem.id == ctx.entity_id)
        )
        if wi is None:
            return EffectorResult(
                effector_name=self.name,
                status="error",
                duration_ms=int((time.monotonic() - start) * 1000),
                error_code="work-item-not-found",
            )

        # Idempotency: if tasks already exist for this work item, skip.
        existing = await ctx.db.scalar(
            select(func.count(Task.id)).where(Task.work_item_id == wi.id)
        )
        if existing and existing > 0:
            return EffectorResult(
                effector_name=self.name,
                status="skipped",
                duration_ms=int((time.monotonic() - start) * 1000),
                detail=f"{existing} tasks already exist",
            )

        titles = _SCAFFOLDS.get(wi.type, _SCAFFOLDS[WorkItemType.FEAT.value])
        created: list[str] = []
        for idx, title in enumerate(titles, start=1):
            ref = f"T-{wi.external_ref}-{idx:02d}"
            task = Task(
                id=uuid.uuid4(),
                work_item_id=wi.id,
                external_ref=ref,
                title=title,
                status=TaskStatus.PROPOSED.value,
                proposer_type="system",
                proposer_id="task_generation",
            )
            ctx.db.add(task)
            created.append(ref)
        await ctx.db.commit()

        logger.info(
            "tasks generated",
            extra={
                "work_item_id": str(wi.id),
                "work_item_ref": wi.external_ref,
                "task_refs": created,
                "count": len(created),
            },
        )
        return EffectorResult(
            effector_name=self.name,
            status="ok",
            duration_ms=int((time.monotonic() - start) * 1000),
            metadata={"task_refs": created, "count": len(created)},
        )
```

### Step 3: Register
**File:** `src/app/modules/ai/lifecycle/effectors/bootstrap.py`
**Action:** Modify

```python
registry.register(
    "work_item:entry:pending_tasks",
    GenerateTasksEffector(),
)
```

### Step 4: Delete the stub
**File:** `src/app/modules/ai/lifecycle/service.py`
**Action:** Modify

Grep for `dispatch_task_generation` and `task-generation dispatched`. Remove both the call site and the function. The effector covers it now; any caller expecting the old log line will see `generate_tasks` instead.

### Step 5: Unit tests
**File:** `tests/modules/ai/lifecycle/effectors/test_task_generation.py`
**Action:** Create

Cases:
- **FEAT scaffold.** Seed a FEAT work item with no tasks → effector produces 3 tasks with the FEAT titles, all `status=proposed`.
- **BUG scaffold.** Same for BUG.
- **IMP scaffold.** Same for IMP (2 tasks).
- **Idempotent.** Seed a work item with 1 pre-existing task → effector returns `status="skipped"`, adds nothing.
- **Missing work item.** Random uuid → `status="error"`, `error_code="work-item-not-found"`.
- **External ref format.** `T-FEAT-042-01`, `T-FEAT-042-02` — deterministic + unique.
- **Unknown work-item type.** Defensively falls back to FEAT scaffold, doesn't raise.

### Step 6: Integration regression
**File:** `tests/integration/test_feat006_e2e.py`
**Action:** Modify (if affected)

The e2e test currently seeds tasks by hand (because the stub created none). With the effector live, after `brief-approved` tasks will already exist. Either:

a) Update the test to assert on effector-generated tasks instead of manually-seeded ones (proper, but changes the test's structure).
b) Register the effector only when a specific env flag is set, so the existing test's manual-seed approach still works (defeats the point).

Go with (a). The test becomes shorter, not longer.

## Files Affected
| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/lifecycle/effectors/task_generation.py` | Create | Effector + scaffolds. |
| `src/app/modules/ai/lifecycle/effectors/bootstrap.py` | Modify | Register. |
| `src/app/modules/ai/lifecycle/service.py` | Modify | Remove stub. |
| `tests/modules/ai/lifecycle/effectors/test_task_generation.py` | Create | Unit tests. |
| `tests/integration/test_feat006_e2e.py` | Modify | Adapt to effector-generated tasks. |

## Edge Cases & Risks
- **External ref collisions.** `T-FEAT-042-01` collides across any two work items with `FEAT-042` as their ref. In practice, work-item external refs are unique (that's what makes them external refs). Enforce via a DB unique constraint on `tasks.external_ref` if not already present.
- **Commit boundary inside effector.** Step 2 commits inside the effector. If the caller (reactor) is already in a transaction, this creates a weird layering. Decide: (a) effector opens its own transaction; (b) effector assumes it owns the session and commits freely. Go with (b) for v1 — simpler, matches the GitHub effector's pattern. Document in `EffectorContext` docstring.
- **Deterministic-to-LLM migration path.** When a real LLM-backed generator lands, it replaces this effector by registering *after* it with the same key, and either this effector is unregistered or its scaffold shrinks to "stub for when LLM is unavailable." Capture that in a comment on `_SCAFFOLDS`.
- **Behavior change in docs.** README's "Getting Started" section says tasks are seeded manually. After T-164 that's no longer true — update in T-172 or a follow-on docs commit.

## Acceptance Verification
- [ ] Effector creates N tasks per the scaffold table.
- [ ] Idempotent on re-fire against a work item with existing tasks.
- [ ] All scaffolds covered by unit tests (FEAT, BUG, IMP, unknown).
- [ ] `dispatch_task_generation` stub deleted.
- [ ] FEAT-006 e2e test updated and passes.
- [ ] `uv run pyright`, `ruff`, full suite green.
