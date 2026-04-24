"""Unit tests for the task-generation effector (FEAT-008/T-164)."""

from __future__ import annotations

import uuid
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import ActorType, TaskStatus, WorkItemStatus, WorkItemType
from app.modules.ai.lifecycle.effectors import EffectorContext
from app.modules.ai.lifecycle.effectors.task_generation import (
    GenerateTasksEffector,
)
from app.modules.ai.models import Task, WorkItem

pytestmark = pytest.mark.asyncio(loop_scope="function")


async def _seed_work_item(db: AsyncSession, *, wi_type: str, external_ref: str | None = None) -> uuid.UUID:
    wi = WorkItem(
        external_ref=external_ref or f"{wi_type}-{uuid.uuid4().hex[:6]}",
        type=wi_type,
        title="scaffold test",
        status=WorkItemStatus.OPEN.value,
        opened_by="admin",
    )
    db.add(wi)
    await db.commit()
    await db.refresh(wi)
    return wi.id


def _ctx(db: AsyncSession, *, work_item_id: uuid.UUID) -> EffectorContext:
    return EffectorContext(
        entity_type="work_item",
        entity_id=work_item_id,
        from_state=None,
        to_state=WorkItemStatus.OPEN.value,
        transition="S1",
        correlation_id=None,
        db=db,
        settings=cast("Any", MagicMock()),
    )


async def _tasks_for(db: AsyncSession, work_item_id: uuid.UUID) -> list[Task]:
    rows = await db.scalars(select(Task).where(Task.work_item_id == work_item_id).order_by(Task.external_ref))
    return list(rows)


async def test_feat_scaffold_creates_three_tasks(db_session: AsyncSession) -> None:
    wi_id = await _seed_work_item(db_session, wi_type=WorkItemType.FEAT.value, external_ref="FEAT-042")
    result = await GenerateTasksEffector().fire(_ctx(db_session, work_item_id=wi_id))

    assert result.status == "ok"
    assert result.metadata["count"] == 3
    tasks = await _tasks_for(db_session, wi_id)
    assert [t.external_ref for t in tasks] == [
        "T-FEAT-042-01",
        "T-FEAT-042-02",
        "T-FEAT-042-03",
    ]
    assert [t.title for t in tasks] == [
        "Investigate + plan",
        "Implement",
        "Review + close",
    ]
    for t in tasks:
        assert t.status == TaskStatus.PROPOSED.value
        assert t.proposer_type == ActorType.AGENT.value
        assert t.proposer_id == "task_generation"


async def test_bug_scaffold_creates_three_tasks(db_session: AsyncSession) -> None:
    wi_id = await _seed_work_item(db_session, wi_type=WorkItemType.BUG.value, external_ref="BUG-007")
    result = await GenerateTasksEffector().fire(_ctx(db_session, work_item_id=wi_id))

    assert result.status == "ok"
    assert result.metadata["count"] == 3
    tasks = await _tasks_for(db_session, wi_id)
    assert [t.title for t in tasks] == [
        "Reproduce + root cause",
        "Fix",
        "Verify + close",
    ]


async def test_imp_scaffold_creates_two_tasks(db_session: AsyncSession) -> None:
    wi_id = await _seed_work_item(db_session, wi_type=WorkItemType.IMP.value, external_ref="IMP-003")
    result = await GenerateTasksEffector().fire(_ctx(db_session, work_item_id=wi_id))

    assert result.status == "ok"
    assert result.metadata["count"] == 2
    tasks = await _tasks_for(db_session, wi_id)
    assert [t.external_ref for t in tasks] == [
        "T-IMP-003-01",
        "T-IMP-003-02",
    ]
    assert [t.title for t in tasks] == [
        "Scope + plan",
        "Apply improvement",
    ]


async def test_idempotent_when_tasks_already_exist(db_session: AsyncSession) -> None:
    wi_id = await _seed_work_item(db_session, wi_type=WorkItemType.FEAT.value, external_ref="FEAT-ID")
    # Pre-seed a single manual task.
    db_session.add(
        Task(
            work_item_id=wi_id,
            external_ref="T-manual",
            title="manual",
            status=TaskStatus.PROPOSED.value,
            proposer_type=ActorType.ADMIN.value,
            proposer_id="admin",
        )
    )
    await db_session.commit()

    result = await GenerateTasksEffector().fire(_ctx(db_session, work_item_id=wi_id))

    assert result.status == "skipped"
    assert result.metadata["existing"] == 1
    tasks = await _tasks_for(db_session, wi_id)
    assert len(tasks) == 1  # no scaffold added


async def test_missing_work_item_returns_error(db_session: AsyncSession) -> None:
    result = await GenerateTasksEffector().fire(_ctx(db_session, work_item_id=uuid.uuid4()))
    assert result.status == "error"
    assert result.error_code == "work-item-not-found"


async def test_external_refs_are_deterministic_and_unique(
    db_session: AsyncSession,
) -> None:
    wi_id = await _seed_work_item(db_session, wi_type=WorkItemType.FEAT.value, external_ref="FEAT-999")
    result = await GenerateTasksEffector().fire(_ctx(db_session, work_item_id=wi_id))
    assert result.status == "ok"

    refs = result.metadata["task_refs"]
    assert refs == ["T-FEAT-999-01", "T-FEAT-999-02", "T-FEAT-999-03"]
    assert len(set(refs)) == len(refs)
