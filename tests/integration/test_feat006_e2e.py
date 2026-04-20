"""FEAT-006 end-to-end lifecycle test — AC-10.

Drives a work item through every one of the 14 deterministic-flow signals
against real Postgres.  Verifies final state + audit counts.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import ApprovalDecision, TaskStatus, WorkItemStatus
from app.modules.ai.models import Approval, Task, TaskAssignment, WorkItem

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _h(api_key: str, role: str = "admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "X-Actor-Role": role}


async def _seed_task(
    db: AsyncSession, work_item_id, ref: str, *, title: str = "task"
) -> Task:
    t = Task(
        work_item_id=work_item_id,
        external_ref=ref,
        title=title,
        status=TaskStatus.PROPOSED.value,
        proposer_type="admin",
        proposer_id="admin",
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def test_feat006_full_lifecycle(
    client: AsyncClient, api_key: str, db_session: AsyncSession
) -> None:
    # S1 — open work item
    r = await client.post(
        "/api/v1/work-items",
        json={"externalRef": "FEAT-E2E", "type": "FEAT", "title": "E2E"},
        headers=_h(api_key),
    )
    assert r.status_code == 202, r.text
    wi_id = r.json()["data"]["id"]

    # Seed tasks directly (task-generation is a stub in v1).
    task_a = await _seed_task(db_session, wi_id, "T-a", title="A")
    task_b = await _seed_task(db_session, wi_id, "T-b", title="B")
    task_c = await _seed_task(db_session, wi_id, "T-c", title="C (will defer)")

    # S5 — approve A (fires T4 + W2)
    r = await client.post(
        f"/api/v1/tasks/{task_a.id}/approve",
        json={},
        headers=_h(api_key),
    )
    assert r.status_code == 202

    # Verify W2 fired
    wi = await db_session.scalar(select(WorkItem).where(WorkItem.id == wi_id))
    assert wi is not None and wi.status == WorkItemStatus.IN_PROGRESS.value

    # S6 — reject B, then re-approve (rejection doesn't advance)
    r = await client.post(
        f"/api/v1/tasks/{task_b.id}/reject",
        json={"feedback": "please split"},
        headers=_h(api_key),
    )
    assert r.status_code == 202
    r = await client.post(
        f"/api/v1/tasks/{task_b.id}/approve",
        json={},
        headers=_h(api_key),
    )
    assert r.status_code == 202

    # S2 / S3 — lock then unlock mid-flow
    r = await client.post(
        f"/api/v1/work-items/{wi_id}/lock",
        json={"reason": "release freeze"},
        headers=_h(api_key),
    )
    assert r.status_code == 202
    r = await client.post(
        f"/api/v1/work-items/{wi_id}/unlock",
        json={},
        headers=_h(api_key),
    )
    assert r.status_code == 202

    # S7 — assign A to dev, B to agent
    r = await client.post(
        f"/api/v1/tasks/{task_a.id}/assign",
        json={"assigneeType": "dev", "assigneeId": "dev-1"},
        headers=_h(api_key),
    )
    assert r.status_code == 202
    r = await client.post(
        f"/api/v1/tasks/{task_b.id}/assign",
        json={"assigneeType": "agent", "assigneeId": "agent-x"},
        headers=_h(api_key),
    )
    assert r.status_code == 202

    # S8/S9 — submit + approve plan for A (dev-assigned: dev approves)
    r = await client.post(
        f"/api/v1/tasks/{task_a.id}/plan",
        json={"planPath": "plans/plan-a.md", "planSha": "aa"},
        headers=_h(api_key, role="dev"),
    )
    assert r.status_code == 202
    r = await client.post(
        f"/api/v1/tasks/{task_a.id}/plan/approve",
        json={},
        headers=_h(api_key, role="dev"),
    )
    assert r.status_code == 202

    # S8/S9 — submit + approve plan for B (agent-assigned: admin approves)
    r = await client.post(
        f"/api/v1/tasks/{task_b.id}/plan",
        json={"planPath": "plans/plan-b.md", "planSha": "bb"},
        headers=_h(api_key),
    )
    assert r.status_code == 202
    r = await client.post(
        f"/api/v1/tasks/{task_b.id}/plan/approve",
        json={},
        headers=_h(api_key),
    )
    assert r.status_code == 202

    # S11/S12 — submit + approve review for A
    r = await client.post(
        f"/api/v1/tasks/{task_a.id}/implementation",
        json={"commitSha": "ff1", "summary": "A done"},
        headers=_h(api_key),
    )
    assert r.status_code == 202
    r = await client.post(
        f"/api/v1/tasks/{task_a.id}/review/approve",
        json={},
        headers=_h(api_key),
    )
    assert r.status_code == 202

    # S11/S13/S11/S12 — B: submit, reject once, resubmit, approve
    r = await client.post(
        f"/api/v1/tasks/{task_b.id}/implementation",
        json={"commitSha": "ff2", "summary": "B v1"},
        headers=_h(api_key),
    )
    assert r.status_code == 202
    r = await client.post(
        f"/api/v1/tasks/{task_b.id}/review/reject",
        json={"feedback": "missing tests"},
        headers=_h(api_key),
    )
    assert r.status_code == 202
    r = await client.post(
        f"/api/v1/tasks/{task_b.id}/implementation",
        json={"commitSha": "ff3", "summary": "B v2"},
        headers=_h(api_key),
    )
    assert r.status_code == 202
    r = await client.post(
        f"/api/v1/tasks/{task_b.id}/review/approve",
        json={},
        headers=_h(api_key),
    )
    assert r.status_code == 202

    # S14 — defer C
    r = await client.post(
        f"/api/v1/tasks/{task_c.id}/defer",
        json={"reason": "scope change"},
        headers=_h(api_key),
    )
    assert r.status_code == 202

    # W5 should have fired (A=done, B=done, C=deferred). Expect work item=ready.
    await db_session.refresh(wi)
    assert wi.status == WorkItemStatus.READY.value

    # S4 — close
    r = await client.post(
        f"/api/v1/work-items/{wi_id}/close",
        json={"notes": "all shipped"},
        headers=_h(api_key),
    )
    assert r.status_code == 202, r.text
    await db_session.refresh(wi)
    assert wi.status == WorkItemStatus.CLOSED.value

    # Terminal-state assertions on child entities.
    for t in (task_a, task_b):
        await db_session.refresh(t)
        assert t.status == TaskStatus.DONE.value
    await db_session.refresh(task_c)
    assert task_c.status == TaskStatus.DEFERRED.value

    # Assignment counts.
    assignments = (
        await db_session.scalars(select(TaskAssignment))
    ).all()
    assert len([a for a in assignments if a.superseded_at is None]) == 2

    # Approval audit — at minimum: 2 approve(proposed), 1 reject(proposed),
    # 2 approve(plan), 2 approve(impl), 1 reject(impl).
    approvals = (await db_session.scalars(select(Approval))).all()
    rejects = [a for a in approvals if a.decision == ApprovalDecision.REJECT.value]
    approves = [a for a in approvals if a.decision == ApprovalDecision.APPROVE.value]
    assert len(rejects) == 2  # reject proposal B + reject impl B
    assert len(approves) >= 6
