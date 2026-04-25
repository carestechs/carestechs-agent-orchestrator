"""FEAT-008/T-173 — reactor invokes ``EffectorRegistry.fire_all``.

These tests pin down the runtime contract behind AC-3 + AC-5: registration
and invocation are equivalent. T-171's startup validator proves coverage
statically; this module proves the reactor actually dispatches at runtime.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import ClassVar

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.ai.enums import TaskStatus, WorkItemStatus
from app.modules.ai.lifecycle import declarations, reactor
from app.modules.ai.lifecycle.effectors.context import (
    EffectorContext,
    EffectorResult,
)
from app.modules.ai.lifecycle.effectors.registry import EffectorRegistry
from app.modules.ai.models import Task, WorkItem
from app.modules.ai.trace import NoopTraceStore

pytestmark = pytest.mark.asyncio(loop_scope="function")


class _RecordingEffector:
    """Effector that captures every fire for later assertion."""

    name: ClassVar[str] = "recording"

    def __init__(self) -> None:
        self.fired_with: list[EffectorContext] = []

    async def fire(self, ctx: EffectorContext) -> EffectorResult:
        self.fired_with.append(ctx)
        return EffectorResult(effector_name=self.name, status="ok", duration_ms=0)


def _event(
    *,
    item_id: uuid.UUID,
    workflow_id: uuid.UUID,
    from_status: str | None,
    to_status: str,
    triggered_by: str = "engine",
) -> reactor.LifecycleWebhookEvent:
    return reactor.LifecycleWebhookEvent(
        delivery_id=uuid.uuid4(),
        event_type="item.transitioned",
        tenant_id=uuid.uuid4(),
        workflow_id=workflow_id,
        item_id=item_id,
        timestamp=datetime.now(UTC),
        data=reactor.LifecycleWebhookData(
            from_status=from_status,
            to_status=to_status,
            triggered_by=triggered_by,
        ),
    )


async def test_reactor_fires_registered_task_effector(
    db_session: AsyncSession,
) -> None:
    wi = WorkItem(
        external_ref=f"FEAT-{uuid.uuid4().hex[:6]}",
        type="FEAT",
        title="t",
        status=WorkItemStatus.IN_PROGRESS.value,
        opened_by="admin",
    )
    db_session.add(wi)
    await db_session.flush()
    engine_item_id = uuid.uuid4()
    task = Task(
        work_item_id=wi.id,
        external_ref="T-T173-a",
        title="x",
        status=TaskStatus.APPROVED.value,
        proposer_type="admin",
        proposer_id="admin",
        engine_item_id=engine_item_id,
    )
    db_session.add(task)
    await db_session.commit()

    eff = _RecordingEffector()
    registry = EffectorRegistry(trace=NoopTraceStore())
    registry.register("task:approved->assigning", eff)

    workflow_id = uuid.uuid4()
    event = _event(
        item_id=engine_item_id,
        workflow_id=workflow_id,
        from_status=TaskStatus.APPROVED.value,
        to_status=TaskStatus.ASSIGNING.value,
    )
    mapping = {workflow_id: declarations.TASK_WORKFLOW_NAME}

    await reactor.handle_transition(
        db_session,
        event,
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
    # ``triggered_by="engine"`` carries no correlation id; reactor passes
    # ``None`` through rather than fabricating one.
    assert ctx.correlation_id is None


async def test_reactor_fires_registered_work_item_effector_on_entry(
    db_session: AsyncSession,
) -> None:
    """``entry:`` keys (no from_state) work end-to-end."""
    engine_item_id = uuid.uuid4()
    wi = WorkItem(
        external_ref=f"FEAT-{uuid.uuid4().hex[:6]}",
        type="FEAT",
        title="t",
        status=WorkItemStatus.OPEN.value,
        opened_by="admin",
        engine_item_id=engine_item_id,
    )
    db_session.add(wi)
    await db_session.commit()

    eff = _RecordingEffector()
    registry = EffectorRegistry(trace=NoopTraceStore())
    registry.register("work_item:entry:open", eff)

    workflow_id = uuid.uuid4()
    event = _event(
        item_id=engine_item_id,
        workflow_id=workflow_id,
        from_status=None,
        to_status=WorkItemStatus.OPEN.value,
    )
    mapping = {workflow_id: declarations.WORK_ITEM_WORKFLOW_NAME}

    await reactor.handle_transition(
        db_session,
        event,
        workflow_name_by_id=mapping,
        registry=registry,
        settings=get_settings(),
    )

    assert len(eff.fired_with) == 1
    ctx = eff.fired_with[0]
    assert ctx.entity_type == "work_item"
    assert ctx.entity_id == wi.id
    assert ctx.from_state is None
    assert ctx.transition == "work_item:entry:open"


async def test_reactor_no_op_when_registry_omitted(
    db_session: AsyncSession,
) -> None:
    """Existing call sites that omit registry must keep working."""
    engine_item_id = uuid.uuid4()
    wi = WorkItem(
        external_ref=f"FEAT-{uuid.uuid4().hex[:6]}",
        type="FEAT",
        title="t",
        status=WorkItemStatus.OPEN.value,
        opened_by="admin",
        engine_item_id=engine_item_id,
    )
    db_session.add(wi)
    await db_session.commit()

    workflow_id = uuid.uuid4()
    event = _event(
        item_id=engine_item_id,
        workflow_id=workflow_id,
        from_status=None,
        to_status=WorkItemStatus.OPEN.value,
    )
    mapping = {workflow_id: declarations.WORK_ITEM_WORKFLOW_NAME}

    # No registry, no settings — reactor should run the rest of the pipeline
    # (status cache, correlation consume, derivations) without raising.
    await reactor.handle_transition(db_session, event, workflow_name_by_id=mapping)


async def test_reactor_skips_dispatch_on_local_cache_miss(
    db_session: AsyncSession,
) -> None:
    """Engine emits a transition for an item the orchestrator never saw — log + skip."""
    eff = _RecordingEffector()
    registry = EffectorRegistry(trace=NoopTraceStore())
    registry.register("task:approved->assigning", eff)

    workflow_id = uuid.uuid4()
    event = _event(
        item_id=uuid.uuid4(),  # not in our DB
        workflow_id=workflow_id,
        from_status=TaskStatus.APPROVED.value,
        to_status=TaskStatus.ASSIGNING.value,
    )
    mapping = {workflow_id: declarations.TASK_WORKFLOW_NAME}

    await reactor.handle_transition(
        db_session,
        event,
        workflow_name_by_id=mapping,
        registry=registry,
        settings=get_settings(),
    )

    assert eff.fired_with == []
