# Implementation Plan: T-173 — Reactor invokes `EffectorRegistry.fire_all` on every transition

## Task Reference
- **Task ID:** T-173
- **Type:** Backend
- **Workflow:** standard
- **Complexity:** S
- **Dependencies:** T-161, T-163, T-164, T-167, T-169, T-171
- **Rationale:** Closes the AC-5 invocation gap surfaced by T-172. Today `RequestAssignmentEffector` is *registered* on `task:entry:assigning` and `GenerateTasksEffector` on `work_item:entry:open`, but nothing in the reactor invokes the registry — registration was satisfying the static check (T-171) without satisfying the runtime claim. This task makes registration and invocation equivalent.

## Overview

The reactor (`handle_transition`) currently does four things on each `item.transitioned` webhook:

1. Materialize the outbox-queued aux row (T-167).
2. Update the local status cache (T-169).
3. Consume the correlation context row (T-133).
4. Fire derivations (W2/W5).

We add a fifth step **between (2) and the derivations**: build an `EffectorContext` from the event and invoke `registry.fire_all(ctx)`. The registry is supplied by the route handler from `app.state.effector_registry` (the lifespan singleton built in T-171). Tests that don't care about effector dispatch pass `registry=None` and the call is a no-op.

This also lets us delete the duplicate `_dispatch_task_generation` site in `service.py` (T-164's pre-`fire_all` shim). After this task, the only direct-dispatch sites are the GitHub effectors (T-162), which remain because they need per-request DI for the `GitHubChecksClient` — their `no_effector` exemptions stay valid.

## Implementation Steps

### Step 1: Extend `handle_transition` to accept the registry

**File:** `src/app/modules/ai/lifecycle/reactor.py`
**Action:** Modify

Add two optional parameters and a new internal step. Keep them optional so existing tests keep working with no signature churn beyond the call site they care about.

```python
from app.config import Settings
from app.modules.ai.lifecycle.effectors.context import EffectorContext
from app.modules.ai.lifecycle.effectors.registry import (
    EffectorRegistry,
    build_transition_key,
)


async def handle_transition(
    db: AsyncSession,
    event: LifecycleWebhookEvent,
    *,
    workflow_name_by_id: dict[uuid.UUID, str] | None = None,
    registry: EffectorRegistry | None = None,
    settings: Settings | None = None,
) -> None:
    ...
    # existing: workflow resolution, _materialize_aux, _update_status_cache,
    # _consume_correlation
    ...

    # NEW: dispatch registered effectors for this transition.
    if registry is not None and settings is not None:
        await _dispatch_effectors(db, event, workflow_name, registry, settings, corr)

    to_status = event.data.to_status
    if workflow_name == declarations.TASK_WORKFLOW_NAME:
        await _handle_task_transition(db, event.item_id, to_status)
    ...
```

The `corr` variable is the same `extract_correlation_id` result already computed earlier in the function for `_materialize_aux`. Pass it through to `EffectorContext.correlation_id`.

Add the helper at module bottom:

```python
async def _dispatch_effectors(
    db: AsyncSession,
    event: LifecycleWebhookEvent,
    workflow_name: str,
    registry: EffectorRegistry,
    settings: Settings,
    correlation_id: uuid.UUID | None,
) -> None:
    """Fire ``registry.fire_all`` for the resolved transition key.

    Looks up the local entity (task or work item) by ``engine_item_id`` to
    populate ``EffectorContext.entity_id`` — effectors expect a *local*
    UUID, not the engine's. Cache miss is logged + skipped (mirrors
    ``_update_status_cache``).
    """
    to_status = event.data.to_status
    from_status = event.data.from_status
    if to_status is None:
        return

    if workflow_name == declarations.TASK_WORKFLOW_NAME:
        entity_type: Literal["work_item", "task"] = "task"
        row = await db.scalar(
            select(Task).where(Task.engine_item_id == event.item_id)
        )
    elif workflow_name == declarations.WORK_ITEM_WORKFLOW_NAME:
        entity_type = "work_item"
        row = await db.scalar(
            select(WorkItem).where(WorkItem.engine_item_id == event.item_id)
        )
    else:
        return

    if row is None:
        logger.info(
            "effector dispatch: %s engine_item_id=%s not found locally; skipping",
            entity_type, event.item_id,
        )
        return

    transition = build_transition_key(entity_type, from_status, to_status)
    ctx = EffectorContext(
        entity_type=entity_type,
        entity_id=row.id,
        from_state=from_status,
        to_state=to_status,
        transition=transition,
        correlation_id=correlation_id,
        db=db,
        settings=settings,
    )
    await registry.fire_all(ctx)
```

`Literal` import: add `from typing import Literal` to the existing imports.

### Step 2: Thread the registry through the route handler

**File:** `src/app/modules/ai/router.py`
**Action:** Modify

The lifecycle webhook handler already pulls `workflow_ids` from `app.state.lifecycle_workflow_ids`. Add the registry + settings:

```python
registry = getattr(request.app.state, "effector_registry", None)
settings = get_settings()  # already imported in this module

await lifecycle_reactor.handle_transition(
    db,
    event,
    workflow_name_by_id=workflow_name_by_id,
    registry=registry,
    settings=settings,
)
```

`request: Request` is already a parameter on the route. `getattr` with default keeps the test surface forgiving — tests that build a bare `FastAPI()` without running the lifespan won't crash.

### Step 3: Delete the duplicate task-generation dispatch

**File:** `src/app/modules/ai/lifecycle/service.py`
**Action:** Modify

T-164 wired `_dispatch_task_generation` to fire `GenerateTasksEffector` directly from the work-item open signal handler. With registry-driven dispatch, the engine's confirmation webhook fires it instead. Remove:

- The function `_dispatch_task_generation` (and its `EffectorContext` build site).
- The call from the open-work-item signal handler.
- Any test in `tests/modules/ai/lifecycle/effectors/test_task_generation.py` that asserts the *direct-dispatch* path — those move to the reactor-dispatch test in Step 5 below.

Engine-absent mode: the open-work-item signal handler does *not* fall back to inline task generation in v1. Document the deferral with a comment pointing at the follow-on:

```python
# Engine-absent mode: task generation is webhook-driven (registry → reactor →
# GenerateTasksEffector). Without an engine, the reactor never fires; seeded
# tasks are the operator's responsibility until a follow-on adds an inline
# fallback. See tasks/FEAT-008-tasks.md T-173.
```

(If keeping the engine-absent inline fallback is preferred at review time, switch the conditional rather than expanding scope. Either is defensible; leaving it deferred keeps the seam clean.)

Update the bootstrap docstring's stale comment that still references the per-request task-generation dispatch site.

### Step 4: Pass `registry=None` in unmodified reactor tests

**Files:**
- `tests/modules/ai/lifecycle/test_reactor.py`
- `tests/modules/ai/lifecycle/test_reactor_aux_materialization.py`
- `tests/modules/ai/lifecycle/test_reactor_status_cache.py`

**Action:** Modify

Every existing call to `reactor.handle_transition(...)` keeps working as-is — the new params default to `None`, so the dispatch step is a no-op. Verify with `uv run pytest tests/modules/ai/lifecycle/`. No code edits should be required.

If any test grows assertions on effector dispatch (none should), build a registry inline:

```python
registry = EffectorRegistry(trace=NoopTraceStore())
register_all_effectors(registry, trace=NoopTraceStore())
await reactor.handle_transition(..., registry=registry, settings=get_settings())
```

### Step 5: New unit test — reactor invokes `fire_all`

**File:** `tests/modules/ai/lifecycle/test_reactor_effector_dispatch.py`
**Action:** Create

```python
"""FEAT-008/T-173 — reactor dispatches the effector registry."""

from __future__ import annotations

import uuid
from typing import ClassVar

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.ai.enums import TaskStatus
from app.modules.ai.lifecycle import declarations, reactor
from app.modules.ai.lifecycle.effectors.context import (
    EffectorContext, EffectorResult,
)
from app.modules.ai.lifecycle.effectors.registry import EffectorRegistry
from app.modules.ai.models import Task, WorkItem
from app.modules.ai.trace import NoopTraceStore

pytestmark = pytest.mark.asyncio(loop_scope="function")


class _RecordingEffector:
    name: ClassVar[str] = "recording"

    def __init__(self) -> None:
        self.fired_with: list[EffectorContext] = []

    async def fire(self, ctx: EffectorContext) -> EffectorResult:
        self.fired_with.append(ctx)
        return EffectorResult(
            effector_name=self.name, status="ok", duration_ms=0,
        )


async def test_reactor_fires_registered_effector(
    db_session: AsyncSession,
) -> None:
    wi = WorkItem(external_ref="FEAT-T173", type="FEAT", title="x",
                  status="open", opened_by="admin")
    db_session.add(wi)
    await db_session.flush()
    engine_item_id = uuid.uuid4()
    task = Task(
        work_item_id=wi.id, external_ref="T-T173-a", title="x",
        status=TaskStatus.APPROVED.value, proposer_type="admin",
        proposer_id="admin", engine_item_id=engine_item_id,
    )
    db_session.add(task)
    await db_session.commit()

    eff = _RecordingEffector()
    registry = EffectorRegistry(trace=NoopTraceStore())
    registry.register("task:approved->assigning", eff)

    workflow_id = uuid.uuid4()
    event = reactor.LifecycleWebhookEvent.model_validate({
        "deliveryId": str(uuid.uuid4()),
        "eventType": "item.transitioned",
        "tenantId": str(uuid.uuid4()),
        "workflowId": str(workflow_id),
        "itemId": str(engine_item_id),
        "timestamp": "2026-04-25T00:00:00Z",
        "data": {
            "fromStatus": TaskStatus.APPROVED.value,
            "toStatus": TaskStatus.ASSIGNING.value,
            "triggeredBy": "engine",
        },
    })
    mapping = {workflow_id: declarations.TASK_WORKFLOW_NAME}

    await reactor.handle_transition(
        db_session, event,
        workflow_name_by_id=mapping,
        registry=registry,
        settings=get_settings(),
    )

    assert len(eff.fired_with) == 1
    ctx = eff.fired_with[0]
    assert ctx.entity_type == "task"
    assert ctx.entity_id == task.id
    assert ctx.from_state == TaskStatus.APPROVED.value
    assert ctx.to_state == TaskStatus.ASSIGNING.value
    assert ctx.transition == "task:approved->assigning"


async def test_reactor_no_op_when_registry_none(
    db_session: AsyncSession,
) -> None:
    """registry=None must be a no-op — preserves existing test ergonomics."""
    # build an event for an unknown engine_item_id; no exception, no log spam.
    event = reactor.LifecycleWebhookEvent.model_validate({
        "deliveryId": str(uuid.uuid4()),
        "eventType": "item.transitioned",
        "tenantId": str(uuid.uuid4()),
        "workflowId": str(uuid.uuid4()),
        "itemId": str(uuid.uuid4()),
        "timestamp": "2026-04-25T00:00:00Z",
        "data": {"fromStatus": "approved", "toStatus": "assigning",
                 "triggeredBy": "engine"},
    })
    await reactor.handle_transition(db_session, event)  # registry omitted
```

Cases:
- **Happy path** — registered effector fires exactly once with the expected context fields populated.
- **`registry=None`** — call returns cleanly without invoking anything.

A third case (engine cache miss) is covered implicitly by the existing `_update_status_cache` test; no need to duplicate.

### Step 6: Strengthen the FEAT-008 invariant-3 e2e

**File:** `tests/integration/test_feat008_reactor_authoritative.py`
**Action:** Modify

T-172 added `test_every_declared_transition_is_covered` (registration-time check). Add a runtime sibling that proves `RequestAssignmentEffector` actually emits an `effector_call` trace when an approve-task webhook arrives:

```python
async def test_request_assignment_effector_fires_at_runtime(
    app: FastAPI,
    client: AsyncClient,
    api_key: str,
    webhook_secret: str,
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    """Approve-task → engine confirms → reactor invokes registry → trace lands."""
    # Same scaffolding as test_aux_flows_through_outbox_under_engine_present:
    # mock engine, seed task in PROPOSED, POST /approve, deliver synthetic
    # webhook with from=proposed to=approved, then a second webhook for
    # approved→assigning. After the second webhook, read the effector trace
    # for the task and assert at least one entry has
    # transition_key="task:approved->assigning"
    # and effector_name="request_assignment".
```

Reuse the JsonlTraceStore tmp-dir pattern from the existing `test_effector_call_trace_includes_transition_key` case.

### Step 7: Verify

```bash
uv run pyright
uv run ruff check src/app/modules/ai/lifecycle/reactor.py src/app/modules/ai/router.py src/app/modules/ai/lifecycle/service.py tests/modules/ai/lifecycle/test_reactor_effector_dispatch.py tests/integration/test_feat008_reactor_authoritative.py
uv run ruff format src/app/modules/ai/lifecycle/reactor.py src/app/modules/ai/router.py src/app/modules/ai/lifecycle/service.py tests/modules/ai/lifecycle/test_reactor_effector_dispatch.py tests/integration/test_feat008_reactor_authoritative.py
uv run pytest tests/modules/ai/lifecycle/ tests/integration/test_feat008_reactor_authoritative.py
```

## Files Affected

| File | Action | Summary |
|------|--------|---------|
| `src/app/modules/ai/lifecycle/reactor.py` | Modify | Accept `registry` + `settings`; add `_dispatch_effectors` step. |
| `src/app/modules/ai/router.py` | Modify | Pass `app.state.effector_registry` + `Settings` into `handle_transition`. |
| `src/app/modules/ai/lifecycle/service.py` | Modify | Remove `_dispatch_task_generation` direct dispatch; document engine-absent deferral. |
| `src/app/modules/ai/lifecycle/effectors/bootstrap.py` | Modify | Update stale docstring referencing the removed direct dispatch. |
| `tests/modules/ai/lifecycle/test_reactor_effector_dispatch.py` | Create | Unit test: reactor invokes `fire_all` once with correct ctx. |
| `tests/modules/ai/lifecycle/test_reactor*.py` | Modify | None expected — new params are optional. Verify only. |
| `tests/integration/test_feat008_reactor_authoritative.py` | Modify | Add runtime trace assertion for `RequestAssignmentEffector`. |
| `tests/modules/ai/lifecycle/effectors/test_task_generation.py` | Modify | Drop the direct-dispatch assertion, keep effector unit tests. |
| `docs/work-items/FEAT-008-effector-registry-and-engine-authority.md` | Modify | Flip `Status` to `Completed`. |

## Edge Cases & Risks

- **Effector exception leakage.** `EffectorRegistry.fire_all` already catches per-effector exceptions and emits an `error` result — that contract is unchanged. The reactor itself MUST NOT catch around `fire_all`; let the registry's logging/tracing speak for itself.
- **Cache miss for engine-only entities.** If the engine emits a transition for an item the orchestrator never created (shouldn't happen under the architecture, but cheap to guard), `_dispatch_effectors` logs and returns — same behavior as `_update_status_cache`.
- **Order of operations.** Effector dispatch fires *after* `_update_status_cache` so effectors can read the local row's authoritative `status`. It fires *before* `_handle_task_transition`'s W2/W5 derivations because those derivations may themselves trigger further engine transitions, and we want the originating transition's effectors to fire first. Don't reorder.
- **Test fixture sprawl.** Most reactor tests don't need a real registry. The `registry=None` default keeps their diff zero. Only the new dispatch test + the e2e build a real registry; the e2e already has the JsonlTraceStore + tmp-dir scaffolding from T-172.
- **GenerateTasksEffector double-fire.** Pre-T-173, this effector fires from `service.py`. Post-T-173, it fires from the reactor. Make sure both sites aren't active at the same time during review — drop the service-side dispatch in the same diff that wires the reactor side.
- **Engine-absent inline fallback (deferred).** This task explicitly does not add inline task generation for engine-absent mode. If review wants that fallback restored, the cleanest place is the existing engine-presence conditional in the open-work-item signal handler — flag in the PR, don't expand T-173 scope.

## Acceptance Verification

- [ ] `handle_transition` accepts `registry` + `settings`; `None` is a graceful no-op.
- [ ] Router threads `app.state.effector_registry` and `get_settings()` through.
- [ ] `_dispatch_effectors` builds `EffectorContext` with `from_state`, `to_state`, `transition`, `correlation_id` populated and calls `registry.fire_all` exactly once.
- [ ] Direct `_dispatch_task_generation` removed; `GenerateTasksEffector` still fires (now via reactor).
- [ ] New unit test asserts effector receives the expected context.
- [ ] `test_feat008_reactor_authoritative.py` proves `RequestAssignmentEffector` produces an `effector_call` trace with `transition_key="task:approved->assigning"` at runtime.
- [ ] `pyright`, `ruff`, full reactor + FEAT-008 e2e suites green.
- [ ] FEAT-008 work-item Status flipped to `Completed` in the same PR.
