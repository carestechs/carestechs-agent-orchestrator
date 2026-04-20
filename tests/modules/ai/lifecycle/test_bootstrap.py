"""Tests for workflow bootstrap (FEAT-006 rc2 / T-129)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import EngineError
from app.modules.ai.lifecycle import bootstrap, declarations
from app.modules.ai.models import EngineWorkflow

pytestmark = pytest.mark.asyncio(loop_scope="function")


class _FakeClient:
    """Records every method call; configurable responses per scenario."""

    def __init__(
        self,
        *,
        create_side_effect: list[Any] | None = None,
        lookup_side_effect: list[Any] | None = None,
    ) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.lookup_calls: list[str] = []
        self._create_effects = list(create_side_effect or [])
        self._lookup_effects = list(lookup_side_effect or [])

    async def create_workflow(
        self,
        *,
        name: str,
        statuses: list[dict[str, Any]],
        transitions: list[dict[str, Any]],
        initial_status: str,
        description: str | None = None,
    ) -> uuid.UUID:
        self.create_calls.append(
            {
                "name": name,
                "statuses": statuses,
                "transitions": transitions,
                "initial_status": initial_status,
            }
        )
        if not self._create_effects:
            return uuid.uuid4()
        effect = self._create_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect

    async def get_workflow_by_name(self, name: str) -> uuid.UUID | None:
        self.lookup_calls.append(name)
        if not self._lookup_effects:
            return None
        effect = self._lookup_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


class TestEnsureWorkflows:
    async def test_cold_start_creates_both(self, db_session: AsyncSession) -> None:
        client = _FakeClient()
        result = await bootstrap.ensure_workflows(db_session, client)  # type: ignore[arg-type]

        assert set(result.keys()) == {
            declarations.WORK_ITEM_WORKFLOW_NAME,
            declarations.TASK_WORKFLOW_NAME,
        }
        assert len(client.create_calls) == 2
        assert client.lookup_calls == []

        cached = (await db_session.scalars(select(EngineWorkflow))).all()
        assert {row.name for row in cached} == set(result.keys())

    async def test_restart_hits_cache(self, db_session: AsyncSession) -> None:
        # Seed cache rows directly
        for name in [
            declarations.WORK_ITEM_WORKFLOW_NAME,
            declarations.TASK_WORKFLOW_NAME,
        ]:
            db_session.add(
                EngineWorkflow(name=name, engine_workflow_id=uuid.uuid4())
            )
        await db_session.commit()

        client = _FakeClient()
        await bootstrap.ensure_workflows(db_session, client)  # type: ignore[arg-type]
        assert client.create_calls == []
        assert client.lookup_calls == []

    async def test_409_triggers_lookup_and_upsert(
        self, db_session: AsyncSession
    ) -> None:
        # Both create calls should 409, both lookups should return a real id.
        existing_work = uuid.uuid4()
        existing_task = uuid.uuid4()
        client = _FakeClient(
            create_side_effect=[
                EngineError("dupe", engine_http_status=409),
                EngineError("dupe", engine_http_status=409),
            ],
            lookup_side_effect=[existing_work, existing_task],
        )
        result = await bootstrap.ensure_workflows(db_session, client)  # type: ignore[arg-type]

        assert set(result.values()) == {existing_work, existing_task}
        cached = {
            row.name: row.engine_workflow_id
            for row in (await db_session.scalars(select(EngineWorkflow))).all()
        }
        assert cached == result

    async def test_409_then_missing_raises(
        self, db_session: AsyncSession
    ) -> None:
        client = _FakeClient(
            create_side_effect=[EngineError("dupe", engine_http_status=409)],
            lookup_side_effect=[None],
        )
        with pytest.raises(EngineError):
            await bootstrap.ensure_workflows(db_session, client)  # type: ignore[arg-type]

    async def test_non_409_error_surfaces(
        self, db_session: AsyncSession
    ) -> None:
        client = _FakeClient(
            create_side_effect=[EngineError("boom", engine_http_status=500)]
        )
        with pytest.raises(EngineError):
            await bootstrap.ensure_workflows(db_session, client)  # type: ignore[arg-type]


class TestDeclarations:
    def test_work_item_workflow_shape(self) -> None:
        states = [s["name"] for s in declarations.WORK_ITEM_STATUSES]
        assert states == ["open", "in_progress", "locked", "ready", "closed"]
        terminal = [s["name"] for s in declarations.WORK_ITEM_STATUSES if s["isTerminal"]]
        assert terminal == ["closed"]

    def test_task_workflow_includes_defer_from_every_non_terminal(self) -> None:
        defer_sources = {
            t["fromStatus"]
            for t in declarations.TASK_TRANSITIONS
            if t["toStatus"] == "deferred"
        }
        expected = {
            "proposed",
            "approved",
            "assigning",
            "planning",
            "plan_review",
            "implementing",
            "impl_review",
        }
        assert defer_sources == expected

    def test_task_workflow_rejection_edges(self) -> None:
        transitions = [
            (t["fromStatus"], t["toStatus"]) for t in declarations.TASK_TRANSITIONS
        ]
        assert ("plan_review", "planning") in transitions
        assert ("impl_review", "implementing") in transitions
