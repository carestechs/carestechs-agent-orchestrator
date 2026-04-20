"""Tests for task proposal/assignment/defer endpoints (FEAT-006 / T-116 + T-119)."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import (
    AssigneeType,
    TaskStatus,
    WorkItemStatus,
)
from app.modules.ai.models import Approval, Task, TaskAssignment, WorkItem

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _headers(api_key: str, role: str = "admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "X-Actor-Role": role}


async def _seed(
    db: AsyncSession,
    *,
    task_status: TaskStatus = TaskStatus.PROPOSED,
    wi_status: WorkItemStatus = WorkItemStatus.OPEN,
    ref: str = "T-1",
) -> tuple[WorkItem, Task]:
    wi = WorkItem(
        external_ref=f"FEAT-{ref}",
        type="FEAT",
        title="t",
        status=wi_status.value,
        opened_by="admin",
    )
    db.add(wi)
    await db.flush()
    task = Task(
        work_item_id=wi.id,
        external_ref=ref,
        title="do",
        status=task_status.value,
        proposer_type="admin",
        proposer_id="admin",
    )
    db.add(task)
    await db.commit()
    await db.refresh(wi)
    await db.refresh(task)
    return wi, task


class TestApprove:
    async def test_approve_fires_t4_and_w2(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        wi, task = await _seed(db_session)
        r = await client.post(
            f"/api/v1/tasks/{task.id}/approve",
            json={},
            headers=_headers(api_key),
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["data"]["status"] == TaskStatus.ASSIGNING.value

        await db_session.refresh(wi)
        assert wi.status == WorkItemStatus.IN_PROGRESS.value

    async def test_wrong_role_forbidden(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        _, task = await _seed(db_session, ref="T-2")
        r = await client.post(
            f"/api/v1/tasks/{task.id}/approve",
            json={},
            headers=_headers(api_key, role="dev"),
        )
        assert r.status_code == 403

    async def test_idempotent_replay(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        _, task = await _seed(db_session, ref="T-3")
        r1 = await client.post(
            f"/api/v1/tasks/{task.id}/approve",
            json={},
            headers=_headers(api_key),
        )
        r2 = await client.post(
            f"/api/v1/tasks/{task.id}/approve",
            json={},
            headers=_headers(api_key),
        )
        assert r1.status_code == 202
        assert r2.status_code == 202
        assert r2.json().get("meta", {}).get("alreadyReceived") is True

        approvals = (
            await db_session.scalars(
                select(Approval).where(Approval.task_id == task.id)
            )
        ).all()
        assert len(list(approvals)) == 1


class TestReject:
    async def test_reject_requires_feedback(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        _, task = await _seed(db_session, ref="T-r1")
        r = await client.post(
            f"/api/v1/tasks/{task.id}/reject",
            json={"feedback": ""},
            headers=_headers(api_key),
        )
        # Project convention: RequestValidationError → 400 Problem Details.
        assert r.status_code == 400

    async def test_reject_records_feedback_and_stays_proposed(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        _, task = await _seed(db_session, ref="T-r2")
        r = await client.post(
            f"/api/v1/tasks/{task.id}/reject",
            json={"feedback": "split it"},
            headers=_headers(api_key),
        )
        assert r.status_code == 202, r.text
        assert r.json()["data"]["status"] == TaskStatus.PROPOSED.value


class TestAssign:
    async def test_assign_from_assigning_moves_to_planning(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        _, task = await _seed(db_session, task_status=TaskStatus.ASSIGNING, ref="T-a1")
        r = await client.post(
            f"/api/v1/tasks/{task.id}/assign",
            json={"assigneeType": "dev", "assigneeId": "dev-1"},
            headers=_headers(api_key),
        )
        assert r.status_code == 202, r.text
        assert r.json()["data"]["status"] == TaskStatus.PLANNING.value

        active = await db_session.scalar(
            select(TaskAssignment).where(
                TaskAssignment.task_id == task.id,
                TaskAssignment.superseded_at.is_(None),
            )
        )
        assert active is not None
        assert active.assignee_type == AssigneeType.DEV.value

    async def test_assign_from_wrong_state_409(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        _, task = await _seed(db_session, ref="T-a2")  # PROPOSED
        r = await client.post(
            f"/api/v1/tasks/{task.id}/assign",
            json={"assigneeType": "dev", "assigneeId": "dev-1"},
            headers=_headers(api_key),
        )
        assert r.status_code == 409


class TestDefer:
    async def test_defer_from_non_terminal(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        _, task = await _seed(db_session, task_status=TaskStatus.PLANNING, ref="T-d1")
        r = await client.post(
            f"/api/v1/tasks/{task.id}/defer",
            json={"reason": "scope change"},
            headers=_headers(api_key),
        )
        assert r.status_code == 202, r.text
        assert r.json()["data"]["status"] == TaskStatus.DEFERRED.value

    async def test_defer_from_done_409(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        _, task = await _seed(db_session, task_status=TaskStatus.DONE, ref="T-d2")
        r = await client.post(
            f"/api/v1/tasks/{task.id}/defer",
            json={},
            headers=_headers(api_key),
        )
        assert r.status_code == 409

    async def test_defer_unknown_404(
        self, client: AsyncClient, api_key: str
    ) -> None:
        r = await client.post(
            f"/api/v1/tasks/{uuid.uuid4()}/defer",
            json={},
            headers=_headers(api_key),
        )
        assert r.status_code == 404
