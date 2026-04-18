"""Tests for POST /api/v1/runs/{id}/signals (FEAT-005 / T-098)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import RunStatus, StopReason
from app.modules.ai.models import Run, RunMemory

pytestmark = pytest.mark.asyncio(loop_scope="function")


async def _seed_run_with_tasks(
    db: AsyncSession,
    *,
    status: RunStatus = RunStatus.RUNNING,
    task_ids: tuple[str, ...] = ("T-001",),
) -> Run:
    run = Run(
        agent_ref="lifecycle-agent@0.1.0",
        agent_definition_hash="sha256:" + "0" * 64,
        intake={},
        status=status,
        started_at=datetime.now(UTC),
        trace_uri="file:///tmp/t.jsonl",
    )
    if status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
        run.stop_reason = StopReason.DONE_NODE
        run.ended_at = datetime.now(UTC)
    db.add(run)
    await db.flush()

    memory = RunMemory(
        run_id=run.id,
        data={"tasks": [{"id": tid, "title": f"Task {tid}"} for tid in task_ids]},
    )
    db.add(memory)
    await db.commit()
    await db.refresh(run)
    return run


class TestHappyPath:
    async def test_accepts_and_returns_dto(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        run = await _seed_run_with_tasks(db_session)

        resp = await client.post(
            f"/api/v1/runs/{run.id}/signals",
            json={
                "name": "implementation-complete",
                "taskId": "T-001",
                "payload": {"commit_sha": "abc1234"},
            },
            headers=auth_headers,
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["data"]["name"] == "implementation-complete"
        assert body["data"]["taskId"] == "T-001"
        assert body["data"]["payload"] == {"commit_sha": "abc1234"}
        assert body.get("meta") is None  # first delivery, no alreadyReceived

    async def test_duplicate_is_idempotent(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        run = await _seed_run_with_tasks(db_session)
        payload: dict[str, Any] = {
            "name": "implementation-complete",
            "taskId": "T-001",
        }
        first = await client.post(
            f"/api/v1/runs/{run.id}/signals", json=payload, headers=auth_headers
        )
        assert first.status_code == 202
        assert first.json().get("meta") is None

        second = await client.post(
            f"/api/v1/runs/{run.id}/signals", json=payload, headers=auth_headers
        )
        assert second.status_code == 202
        assert second.json()["meta"] == {"alreadyReceived": True}
        assert second.json()["data"]["id"] == first.json()["data"]["id"]


class TestRejections:
    async def test_unknown_signal_name_rejected(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        run = await _seed_run_with_tasks(db_session)
        resp = await client.post(
            f"/api/v1/runs/{run.id}/signals",
            json={"name": "mystery-signal", "taskId": "T-001"},
            headers=auth_headers,
        )
        # Project maps validation errors to 400 RFC 7807.
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert "name" in body.get("errors", {})

    async def test_unknown_run(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        ghost = uuid.uuid4()
        resp = await client.post(
            f"/api/v1/runs/{ghost}/signals",
            json={"name": "implementation-complete", "taskId": "T-001"},
            headers=auth_headers,
        )
        assert resp.status_code == 404, resp.text

    async def test_unknown_task(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        run = await _seed_run_with_tasks(db_session, task_ids=("T-001",))
        resp = await client.post(
            f"/api/v1/runs/{run.id}/signals",
            json={"name": "implementation-complete", "taskId": "T-999"},
            headers=auth_headers,
        )
        assert resp.status_code == 404, resp.text

    async def test_terminal_run_returns_409(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        run = await _seed_run_with_tasks(db_session, status=RunStatus.COMPLETED)
        resp = await client.post(
            f"/api/v1/runs/{run.id}/signals",
            json={"name": "implementation-complete", "taskId": "T-001"},
            headers=auth_headers,
        )
        assert resp.status_code == 409, resp.text

    async def test_unauthenticated(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        run = await _seed_run_with_tasks(db_session)
        resp = await client.post(
            f"/api/v1/runs/{run.id}/signals",
            json={"name": "implementation-complete", "taskId": "T-001"},
        )
        assert resp.status_code == 401
