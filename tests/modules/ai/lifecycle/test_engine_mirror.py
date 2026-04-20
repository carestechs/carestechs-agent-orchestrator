"""Engine mirror-write integration for work-item transitions.

FEAT-006 rc2 / T-131a + T-132a: exercises the optional engine-mirror
path.  When a fake engine client is passed, transitions should call
``create_item`` on open and ``transition_item`` on each subsequent
state change.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import WorkItemStatus, WorkItemType
from app.modules.ai.lifecycle import work_items as wi_svc

pytestmark = pytest.mark.asyncio(loop_scope="function")


class _RecordingEngine:
    """Fake FlowEngineLifecycleClient — records every call."""

    def __init__(self) -> None:
        self.created_items: list[dict[str, Any]] = []
        self.transitions: list[dict[str, Any]] = []
        self._next_item_id = uuid.uuid4()

    async def create_item(
        self,
        *,
        workflow_id: uuid.UUID,
        title: str,
        external_ref: str,
        metadata: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        item_id = uuid.uuid4()
        self.created_items.append(
            {
                "workflow_id": workflow_id,
                "title": title,
                "external_ref": external_ref,
                "metadata": metadata or {},
                "item_id": item_id,
            }
        )
        return item_id

    async def transition_item(
        self,
        *,
        item_id: uuid.UUID,
        to_status: str,
        correlation_id: uuid.UUID,
        actor: str | None = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        self.transitions.append(
            {
                "item_id": item_id,
                "to_status": to_status,
                "correlation_id": correlation_id,
                "actor": actor,
            }
        )
        return {"item": {"currentStatus": {"name": to_status}}}


async def _open(
    db: AsyncSession,
    engine: _RecordingEngine,
    workflow_id: uuid.UUID,
    ref: str = "FEAT-ENG",
):
    wi = await wi_svc.open_work_item(
        db,
        external_ref=ref,
        type=WorkItemType.FEAT,
        title="engine demo",
        source_path=None,
        opened_by="admin",
        engine=engine,  # type: ignore[arg-type]
        engine_workflow_id=workflow_id,
    )
    await db.commit()
    return wi


class TestOpenWithEngine:
    async def test_creates_engine_item_and_stores_id(
        self, db_session: AsyncSession
    ) -> None:
        engine = _RecordingEngine()
        wf = uuid.uuid4()
        wi = await _open(db_session, engine, wf)
        assert len(engine.created_items) == 1
        call = engine.created_items[0]
        assert call["workflow_id"] == wf
        assert call["external_ref"] == "FEAT-ENG"
        assert wi.engine_item_id == call["item_id"]

    async def test_open_without_engine_stays_local(
        self, db_session: AsyncSession
    ) -> None:
        wi = await wi_svc.open_work_item(
            db_session,
            external_ref="FEAT-LOCAL",
            type=WorkItemType.FEAT,
            title="local only",
            source_path=None,
            opened_by="admin",
        )
        await db_session.commit()
        assert wi.engine_item_id is None


class TestTransitionsMirrorToEngine:
    async def test_lock_mirrors(self, db_session: AsyncSession) -> None:
        engine = _RecordingEngine()
        wf = uuid.uuid4()
        wi = await _open(db_session, engine, wf, ref="FEAT-L1")
        await wi_svc.maybe_advance_to_in_progress(
            db_session, wi.id, engine=engine  # type: ignore[arg-type]
        )
        await db_session.commit()

        engine.transitions.clear()
        await wi_svc.lock_work_item(
            db_session, wi.id, actor="admin", engine=engine  # type: ignore[arg-type]
        )
        await db_session.commit()

        assert len(engine.transitions) == 1
        assert engine.transitions[0]["to_status"] == WorkItemStatus.LOCKED.value
        assert engine.transitions[0]["item_id"] == wi.engine_item_id

    async def test_transition_without_engine_is_local_only(
        self, db_session: AsyncSession
    ) -> None:
        # Open without engine → engine_item_id is None
        wi = await wi_svc.open_work_item(
            db_session,
            external_ref="FEAT-NE",
            type=WorkItemType.FEAT,
            title="x",
            source_path=None,
            opened_by="admin",
        )
        await db_session.commit()
        # Even if engine client is passed later, engine_item_id is None so
        # the mirror is skipped.
        engine = _RecordingEngine()
        await wi_svc.maybe_advance_to_in_progress(
            db_session, wi.id, engine=engine  # type: ignore[arg-type]
        )
        await db_session.commit()
        assert engine.transitions == []


class TestEngineFailureDoesNotBlockLocal:
    async def test_transition_swallows_engine_errors(
        self, db_session: AsyncSession
    ) -> None:
        class _ErroringEngine(_RecordingEngine):
            async def transition_item(self, **kwargs: Any) -> dict[str, Any]:
                raise RuntimeError("engine down")

        engine = _ErroringEngine()
        wf = uuid.uuid4()
        wi = await _open(db_session, engine, wf, ref="FEAT-ERR")
        await wi_svc.maybe_advance_to_in_progress(
            db_session, wi.id, engine=engine  # type: ignore[arg-type]
        )
        await db_session.commit()
        await db_session.refresh(wi)
        # Local state advanced even though the mirror failed.
        assert wi.status == WorkItemStatus.IN_PROGRESS.value
