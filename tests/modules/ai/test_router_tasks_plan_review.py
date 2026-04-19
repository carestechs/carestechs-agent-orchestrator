"""Tests for plan + review endpoints (FEAT-006 / T-117 + T-118)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import AssigneeType, TaskStatus
from app.modules.ai.models import (
    Task,
    TaskAssignment,
    TaskImplementation,
    TaskPlan,
    WorkItem,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _h(api_key: str, role: str = "admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "X-Actor-Role": role}


async def _seed(
    db: AsyncSession,
    *,
    status: TaskStatus,
    assignee: AssigneeType | None = None,
    ref: str = "T-1",
) -> Task:
    wi = WorkItem(
        external_ref=f"FEAT-{ref}",
        type="FEAT",
        title="t",
        status="in_progress",
        opened_by="admin",
    )
    db.add(wi)
    await db.flush()
    task = Task(
        work_item_id=wi.id,
        external_ref=ref,
        title="do",
        status=status.value,
        proposer_type="admin",
        proposer_id="admin",
    )
    db.add(task)
    await db.flush()
    if assignee is not None:
        db.add(
            TaskAssignment(
                task_id=task.id,
                assignee_type=assignee.value,
                assignee_id=f"{assignee.value}-1",
                assigned_by="admin",
            )
        )
    await db.commit()
    await db.refresh(task)
    return task


class TestPlanSubmit:
    async def test_submit_inserts_task_plan(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        task = await _seed(db_session, status=TaskStatus.PLANNING, assignee=AssigneeType.DEV)
        r = await client.post(
            f"/api/v1/tasks/{task.id}/plan",
            json={"planPath": "plans/plan-T-1.md", "planSha": "abc"},
            headers=_h(api_key, role="dev"),
        )
        assert r.status_code == 202, r.text
        assert r.json()["data"]["status"] == TaskStatus.PLAN_REVIEW.value

        plans = (
            await db_session.scalars(
                select(TaskPlan).where(TaskPlan.task_id == task.id)
            )
        ).all()
        assert len(list(plans)) == 1


class TestPlanApproveMatrix:
    async def test_dev_assigned_requires_dev(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        task = await _seed(
            db_session, status=TaskStatus.PLAN_REVIEW, assignee=AssigneeType.DEV, ref="T-pa1"
        )
        # Admin trying to approve a dev-assigned plan → 409 (matrix mismatch)
        r = await client.post(
            f"/api/v1/tasks/{task.id}/plan/approve",
            json={},
            headers=_h(api_key, role="admin"),
        )
        assert r.status_code == 409, r.text

        r2 = await client.post(
            f"/api/v1/tasks/{task.id}/plan/approve",
            json={},
            headers=_h(api_key, role="dev"),
        )
        assert r2.status_code == 202

    async def test_agent_assigned_requires_admin(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        task = await _seed(
            db_session, status=TaskStatus.PLAN_REVIEW, assignee=AssigneeType.AGENT, ref="T-pa2"
        )
        r = await client.post(
            f"/api/v1/tasks/{task.id}/plan/approve",
            json={},
            headers=_h(api_key, role="dev"),
        )
        assert r.status_code == 409

        r2 = await client.post(
            f"/api/v1/tasks/{task.id}/plan/approve",
            json={},
            headers=_h(api_key, role="admin"),
        )
        assert r2.status_code == 202


class TestPlanReject:
    async def test_reject_requires_feedback(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        task = await _seed(
            db_session, status=TaskStatus.PLAN_REVIEW, assignee=AssigneeType.DEV, ref="T-pr1"
        )
        r = await client.post(
            f"/api/v1/tasks/{task.id}/plan/reject",
            json={"feedback": ""},
            headers=_h(api_key, role="dev"),
        )
        assert r.status_code == 400  # project handler converts 422 → 400

    async def test_reject_returns_to_planning(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        task = await _seed(
            db_session, status=TaskStatus.PLAN_REVIEW, assignee=AssigneeType.DEV, ref="T-pr2"
        )
        r = await client.post(
            f"/api/v1/tasks/{task.id}/plan/reject",
            json={"feedback": "split"},
            headers=_h(api_key, role="dev"),
        )
        assert r.status_code == 202, r.text
        assert r.json()["data"]["status"] == TaskStatus.PLANNING.value


class TestImplementationSubmit:
    async def test_submit_inserts_task_implementation(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        task = await _seed(
            db_session, status=TaskStatus.IMPLEMENTING, assignee=AssigneeType.AGENT, ref="T-i1"
        )
        r = await client.post(
            f"/api/v1/tasks/{task.id}/implementation",
            json={"prUrl": "https://g/pr/1", "commitSha": "def", "summary": "done"},
            headers=_h(api_key, role="admin"),
        )
        assert r.status_code == 202, r.text
        assert r.json()["data"]["status"] == TaskStatus.IMPL_REVIEW.value

        impls = (
            await db_session.scalars(
                select(TaskImplementation).where(TaskImplementation.task_id == task.id)
            )
        ).all()
        assert len(list(impls)) == 1


class TestReviewApprove:
    async def test_solo_dev_requires_admin(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        task = await _seed(
            db_session, status=TaskStatus.IMPL_REVIEW, assignee=AssigneeType.DEV, ref="T-ra1"
        )
        r = await client.post(
            f"/api/v1/tasks/{task.id}/review/approve",
            json={},
            headers=_h(api_key, role="dev"),
        )
        # solo_dev_mode defaults True → expected role is admin → dev gets 409
        assert r.status_code == 409

        r2 = await client.post(
            f"/api/v1/tasks/{task.id}/review/approve",
            json={},
            headers=_h(api_key, role="admin"),
        )
        assert r2.status_code == 202
        assert r2.json()["data"]["status"] == TaskStatus.DONE.value


class TestReviewReject:
    async def test_reject_loops_back(
        self, client: AsyncClient, api_key: str, db_session: AsyncSession
    ) -> None:
        task = await _seed(
            db_session, status=TaskStatus.IMPL_REVIEW, assignee=AssigneeType.DEV, ref="T-rr1"
        )
        r = await client.post(
            f"/api/v1/tasks/{task.id}/review/reject",
            json={"feedback": "missing tests"},
            headers=_h(api_key, role="admin"),
        )
        assert r.status_code == 202, r.text
        assert r.json()["data"]["status"] == TaskStatus.IMPLEMENTING.value
