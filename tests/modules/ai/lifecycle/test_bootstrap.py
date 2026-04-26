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


_TENANT = uuid.uuid4()


class _FakeClient:
    """Records every method call; configurable responses per scenario."""

    def __init__(
        self,
        *,
        create_side_effect: list[Any] | None = None,
        lookup_side_effect: list[Any] | None = None,
        recognize_side_effect: list[Any] | None = None,
    ) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.lookup_calls: list[str] = []
        self.recognize_calls: list[uuid.UUID] = []
        self._create_effects = list(create_side_effect or [])
        self._lookup_effects = list(lookup_side_effect or [])
        # ``recognize_side_effect`` defaults to "engine recognizes everything"
        # so cache-hit tests that don't care about validation just pass through.
        self._recognize_effects = list(recognize_side_effect or [])

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

    async def get_workflow_by_id(self, workflow_id: uuid.UUID) -> bool:
        self.recognize_calls.append(workflow_id)
        if not self._recognize_effects:
            return True
        effect = self._recognize_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


class TestEnsureWorkflows:
    async def test_cold_start_creates_both(self, db_session: AsyncSession) -> None:
        client = _FakeClient()
        result = await bootstrap.ensure_workflows(
            db_session,
            client,  # type: ignore[arg-type]
            tenant_id=_TENANT,
        )

        assert set(result.keys()) == {
            declarations.WORK_ITEM_WORKFLOW_NAME,
            declarations.TASK_WORKFLOW_NAME,
        }
        assert len(client.create_calls) == 2
        assert client.lookup_calls == []

        cached = (await db_session.scalars(select(EngineWorkflow))).all()
        assert {row.name for row in cached} == set(result.keys())
        assert {row.tenant_id for row in cached} == {_TENANT}

    async def test_restart_hits_cache(self, db_session: AsyncSession) -> None:
        # Seed cache rows directly
        for name in [
            declarations.WORK_ITEM_WORKFLOW_NAME,
            declarations.TASK_WORKFLOW_NAME,
        ]:
            db_session.add(
                EngineWorkflow(
                    tenant_id=_TENANT,
                    name=name,
                    engine_workflow_id=uuid.uuid4(),
                )
            )
        await db_session.commit()

        client = _FakeClient()
        await bootstrap.ensure_workflows(
            db_session,
            client,  # type: ignore[arg-type]
            tenant_id=_TENANT,
        )
        assert client.create_calls == []
        assert client.lookup_calls == []
        # BUG-002: each cache hit is validated with one engine round-trip.
        assert len(client.recognize_calls) == 2

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
        result = await bootstrap.ensure_workflows(
            db_session,
            client,  # type: ignore[arg-type]
            tenant_id=_TENANT,
        )

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
            await bootstrap.ensure_workflows(
                db_session,
                client,  # type: ignore[arg-type]
                tenant_id=_TENANT,
            )

    async def test_non_409_error_surfaces(
        self, db_session: AsyncSession
    ) -> None:
        client = _FakeClient(
            create_side_effect=[EngineError("boom", engine_http_status=500)]
        )
        with pytest.raises(EngineError):
            await bootstrap.ensure_workflows(
                db_session,
                client,  # type: ignore[arg-type]
                tenant_id=_TENANT,
            )

    async def test_tenant_change_does_not_return_other_tenants_id(
        self, db_session: AsyncSession
    ) -> None:
        """BUG-002 regression: switching tenants must not return tenant A's id.

        The pre-fix behavior was: cache hit on ``name`` only → return
        tenant A's ``engine_workflow_id`` to a tenant B caller, every
        downstream transition 404s. The fix scopes the cache by
        ``(tenant_id, name)`` so each tenant gets its own row.
        """
        tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
        uuid_a = uuid.uuid4()
        # Seed tenant A's full cache.
        for name in (
            declarations.WORK_ITEM_WORKFLOW_NAME,
            declarations.TASK_WORKFLOW_NAME,
        ):
            db_session.add(
                EngineWorkflow(
                    tenant_id=tenant_a, name=name, engine_workflow_id=uuid_a
                )
            )
        await db_session.commit()

        # Tenant B has empty cache → both workflows must be created fresh.
        new_b = [uuid.uuid4(), uuid.uuid4()]
        client = _FakeClient(create_side_effect=list(new_b))
        result = await bootstrap.ensure_workflows(
            db_session,
            client,  # type: ignore[arg-type]
            tenant_id=tenant_b,
        )

        assert set(result.values()) == set(new_b)
        rows = (await db_session.scalars(select(EngineWorkflow))).all()
        # Tenant A's rows untouched; tenant B's rows added.
        assert {(r.tenant_id, r.engine_workflow_id) for r in rows} >= {
            (tenant_a, uuid_a),
        }
        assert {r.tenant_id for r in rows if r.engine_workflow_id in new_b} == {
            tenant_b
        }
        # Tenant B should never have looked at tenant A's cached ids.
        assert client.recognize_calls == []

    async def test_stale_cache_404_triggers_re_resolve(
        self, db_session: AsyncSession
    ) -> None:
        """BUG-002 regression: a cached id the engine no longer recognizes
        is dropped and re-resolved transparently."""
        stale_id = uuid.uuid4()
        new_ids = [uuid.uuid4(), uuid.uuid4()]
        # Seed a stale row for the first declared workflow only; the second
        # workflow stays cold so it goes through normal create path.
        first_name = declarations.ALL_WORKFLOWS[0]["name"]
        db_session.add(
            EngineWorkflow(
                tenant_id=_TENANT,
                name=first_name,
                engine_workflow_id=stale_id,
            )
        )
        await db_session.commit()

        # Engine doesn't recognize the stale id (False on first
        # recognize call); subsequent calls (none expected for the
        # cold second workflow) pass through.
        client = _FakeClient(
            create_side_effect=list(new_ids),
            recognize_side_effect=[False],
        )
        result = await bootstrap.ensure_workflows(
            db_session,
            client,  # type: ignore[arg-type]
            tenant_id=_TENANT,
        )

        assert result[first_name] == new_ids[0]
        # Stale row replaced.
        rows = (
            await db_session.scalars(
                select(EngineWorkflow).where(
                    EngineWorkflow.tenant_id == _TENANT,
                    EngineWorkflow.name == first_name,
                )
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].engine_workflow_id == new_ids[0]
        assert client.recognize_calls == [stale_id]


class TestSettingsValidator:
    """BUG-002: lifespan must refuse to boot if tenant id is missing."""

    def test_lifecycle_url_without_tenant_id_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pydantic import ValidationError

        from app.config import Settings

        monkeypatch.setenv("FLOW_ENGINE_LIFECYCLE_BASE_URL", "http://engine.test")
        monkeypatch.setenv("FLOW_ENGINE_TENANT_API_KEY", "k")
        monkeypatch.delenv("FLOW_ENGINE_TENANT_ID", raising=False)
        with pytest.raises(ValidationError, match="FLOW_ENGINE_TENANT_ID"):
            Settings()  # type: ignore[call-arg]

    def test_lifecycle_url_without_api_key_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pydantic import ValidationError

        from app.config import Settings

        monkeypatch.setenv("FLOW_ENGINE_LIFECYCLE_BASE_URL", "http://engine.test")
        monkeypatch.delenv("FLOW_ENGINE_TENANT_API_KEY", raising=False)
        monkeypatch.setenv("FLOW_ENGINE_TENANT_ID", str(uuid.uuid4()))
        with pytest.raises(ValidationError, match="FLOW_ENGINE_TENANT_API_KEY"):
            Settings()  # type: ignore[call-arg]

    def test_lifecycle_url_with_both_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.config import Settings

        monkeypatch.setenv("FLOW_ENGINE_LIFECYCLE_BASE_URL", "http://engine.test")
        monkeypatch.setenv("FLOW_ENGINE_TENANT_API_KEY", "k")
        monkeypatch.setenv("FLOW_ENGINE_TENANT_ID", str(uuid.uuid4()))
        s = Settings()  # type: ignore[call-arg]
        assert s.flow_engine_tenant_id is not None


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
