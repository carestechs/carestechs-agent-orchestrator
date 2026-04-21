"""Round-trip + unique-constraint tests for ``PendingAuxWrite`` (T-165)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.models import PendingAuxWrite

pytestmark = pytest.mark.asyncio


async def test_roundtrip(db_session: AsyncSession) -> None:
    corr = uuid.uuid4()
    row = PendingAuxWrite(
        correlation_id=corr,
        signal_name="submit-implementation",
        entity_type="task",
        entity_id=uuid.uuid4(),
        payload={
            "aux_type": "task_implementation",
            "pr_url": "https://github.com/x/y/pull/1",
            "commit_sha": "abc",
            "summary": "done",
            "submitted_by": "dev-1",
        },
    )
    db_session.add(row)
    await db_session.commit()

    loaded = await db_session.scalar(
        select(PendingAuxWrite).where(PendingAuxWrite.correlation_id == corr)
    )
    assert loaded is not None
    assert loaded.signal_name == "submit-implementation"
    assert loaded.entity_type == "task"
    assert loaded.payload["aux_type"] == "task_implementation"
    assert loaded.enqueued_at is not None


async def test_correlation_id_unique(db_session: AsyncSession) -> None:
    corr = uuid.uuid4()
    db_session.add(
        PendingAuxWrite(
            correlation_id=corr,
            signal_name="x",
            entity_type="task",
            entity_id=uuid.uuid4(),
            payload={"aux_type": "approval"},
        )
    )
    await db_session.commit()

    db_session.add(
        PendingAuxWrite(
            correlation_id=corr,
            signal_name="x",
            entity_type="task",
            entity_id=uuid.uuid4(),
            payload={"aux_type": "approval"},
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
