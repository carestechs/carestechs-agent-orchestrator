"""FEAT-008 AC-12 — reactor-authoritative invariants end-to-end.

This test is the canonical proof that engine-as-authority holds. If a
future change to the service layer or reactor breaks one of the
invariants below, this test fails before anything else.

Invariants proved:

* **Aux via outbox.** Under engine-present mode, aux rows (Approval,
  TaskImplementation) are *not* written by the signal adapter — they
  land via the reactor after the engine's ``item.transitioned``
  webhook arrives and ``_materialize_aux`` drains the outbox.

* **Status cache via reactor.** The local ``tasks.status`` column is
  updated only by the reactor, reflecting the engine's authoritative
  ``to_status``. A stale-read window between signal-202 and webhook
  arrival is accepted — the final state is what this test asserts.

* **Engine-absent fallback.** Without an engine client configured, the
  pre-FEAT-008 inline-write path is preserved end-to-end. Runs in the
  default suite (no ``requires_engine`` mark).

Invariant 3 ("every transition fires an effector or is exempt") is
deferred — it depends on T-162 (GitHub check effector) and T-171
(startup validator). A follow-on extends this module once those land.

Do not weaken these assertions to make a flaky test pass —
investigate the regression instead.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.webhook_auth import sign_body
from app.modules.ai.dependencies import get_lifecycle_engine_client
from app.modules.ai.enums import TaskStatus, WorkItemStatus
from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient
from app.modules.ai.models import (
    Approval,
    PendingAuxWrite,
    Task,
    TaskImplementation,
    WorkItem,
)
from tests.integration._reactor_helpers import (
    await_reactor,
    await_task_status,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _h(api_key: str, role: str = "admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "X-Actor-Role": role}


@pytest.fixture(autouse=True)
def force_solo_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOLO_DEV_MODE", "true")
    get_settings.cache_clear()


def _mock_engine() -> Any:
    mock = AsyncMock(spec=FlowEngineLifecycleClient)
    mock.transition_item = AsyncMock(return_value=None)
    mock.create_item = AsyncMock(return_value=uuid.uuid4())
    return mock


def _inject_engine(app: FastAPI, engine: Any) -> None:
    app.dependency_overrides[get_lifecycle_engine_client] = lambda: engine


async def _seed_task(
    db: AsyncSession,
    *,
    work_item_id: uuid.UUID,
    ref: str,
    status: TaskStatus = TaskStatus.PROPOSED,
    engine_item_id: uuid.UUID | None = None,
) -> Task:
    task = Task(
        work_item_id=work_item_id,
        external_ref=ref,
        title=ref,
        status=status.value,
        proposer_type="admin",
        proposer_id="admin",
        engine_item_id=engine_item_id or uuid.uuid4(),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def _deliver_webhook(
    client: AsyncClient,
    *,
    webhook_secret: str,
    item_id: uuid.UUID,
    correlation_id: uuid.UUID | None,
    from_status: str | None,
    to_status: str,
) -> None:
    triggered_by = (
        f"orchestrator-corr:{correlation_id}"
        if correlation_id is not None
        else "engine"
    )
    body_dict = {
        "deliveryId": str(uuid.uuid4()),
        "eventType": "item.transitioned",
        "tenantId": str(uuid.uuid4()),
        "workflowId": str(uuid.uuid4()),
        "itemId": str(item_id),
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data": {
            "fromStatus": from_status,
            "toStatus": to_status,
            "triggeredBy": triggered_by,
        },
    }
    body = json.dumps(body_dict).encode()
    resp = await client.post(
        "/hooks/engine/lifecycle/item-transitioned",
        content=body,
        headers={
            "X-FlowEngine-Signature": sign_body(body, webhook_secret),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 202, resp.text


async def _latest_outbox(
    db: AsyncSession, task_id: uuid.UUID
) -> PendingAuxWrite | None:
    return await db.scalar(
        select(PendingAuxWrite)
        .where(PendingAuxWrite.entity_id == task_id)
        .order_by(PendingAuxWrite.enqueued_at.desc())
    )


# ---------------------------------------------------------------------------
# Invariant 1: aux rows flow through the outbox
# ---------------------------------------------------------------------------


async def test_aux_flows_through_outbox_under_engine_present(
    app: FastAPI,
    client: AsyncClient,
    api_key: str,
    webhook_secret: str,
    db_session: AsyncSession,
) -> None:
    engine = _mock_engine()
    _inject_engine(app, engine)

    r = await client.post(
        "/api/v1/work-items",
        json={"externalRef": "FEAT-R1", "type": "FEAT", "title": "E2E"},
        headers=_h(api_key),
    )
    assert r.status_code == 202, r.text
    wi_id = uuid.UUID(r.json()["data"]["id"])

    engine_item_id = uuid.uuid4()
    task = await _seed_task(
        db_session, work_item_id=wi_id, ref="T-R1-a", engine_item_id=engine_item_id
    )
    task_id = task.id

    r = await client.post(
        f"/api/v1/tasks/{task_id}/approve", json={}, headers=_h(api_key)
    )
    assert r.status_code == 202, r.text

    # Invariant: signal commit wrote an outbox row, not an Approval row.
    pending = await _latest_outbox(db_session, task_id)
    assert pending is not None, "signal must enqueue PendingAuxWrite"
    assert pending.payload["aux_type"] == "approval"
    correlation_id = pending.correlation_id

    approvals_before = await db_session.scalar(
        select(func.count())
        .select_from(Approval)
        .where(Approval.task_id == task_id)
    )
    assert approvals_before == 0, "Approval must land via the reactor, not inline"

    # Deliver matching engine webhook (APPROVED → reactor materializes).
    await _deliver_webhook(
        client,
        webhook_secret=webhook_secret,
        item_id=engine_item_id,
        correlation_id=correlation_id,
        from_status=TaskStatus.PROPOSED.value,
        to_status=TaskStatus.APPROVED.value,
    )

    # Outbox drained; Approval materialized.
    async def outbox_drained(s: AsyncSession) -> bool:
        orphan = await s.scalar(
            select(PendingAuxWrite).where(
                PendingAuxWrite.correlation_id == correlation_id
            )
        )
        return orphan is None

    await await_reactor(
        db_session, outbox_drained, description="outbox drained by reactor"
    )

    approval = await db_session.scalar(
        select(Approval).where(Approval.task_id == task_id)
    )
    assert approval is not None
    assert approval.stage == "proposed"
    assert approval.decision == "approve"


# ---------------------------------------------------------------------------
# Invariant 2: status cache is reactor-managed
# ---------------------------------------------------------------------------


async def test_status_cache_updates_only_via_reactor(
    app: FastAPI,
    client: AsyncClient,
    api_key: str,
    webhook_secret: str,
    db_session: AsyncSession,
) -> None:
    engine = _mock_engine()
    _inject_engine(app, engine)

    wi = WorkItem(
        external_ref=f"FEAT-{uuid.uuid4().hex[:6]}",
        type="FEAT",
        title="e2e",
        status=WorkItemStatus.IN_PROGRESS.value,
        opened_by="admin",
    )
    db_session.add(wi)
    await db_session.flush()
    engine_item_id = uuid.uuid4()
    task = await _seed_task(
        db_session,
        work_item_id=wi.id,
        ref="T-R2-impl",
        status=TaskStatus.IMPLEMENTING,
        engine_item_id=engine_item_id,
    )
    task_id = task.id

    r = await client.post(
        f"/api/v1/tasks/{task_id}/implementation",
        json={
            "prUrl": "https://github.com/a/b/pull/1",
            "commitSha": "deadbeef",
            "summary": "e2e",
        },
        headers=_h(api_key),
    )
    assert r.status_code == 202, r.text

    # Stale-read window: status still IMPLEMENTING until the webhook fires.
    fresh = await db_session.scalar(select(Task).where(Task.id == task_id))
    assert fresh is not None
    await db_session.refresh(fresh)
    assert fresh.status == TaskStatus.IMPLEMENTING.value, (
        "signal adapter must not mutate status under engine-present"
    )

    pending = await _latest_outbox(db_session, task_id)
    assert pending is not None
    correlation_id = pending.correlation_id

    await _deliver_webhook(
        client,
        webhook_secret=webhook_secret,
        item_id=engine_item_id,
        correlation_id=correlation_id,
        from_status=TaskStatus.IMPLEMENTING.value,
        to_status=TaskStatus.IMPL_REVIEW.value,
    )

    # Cache converges to the engine's authoritative state.
    await await_task_status(db_session, task_id, TaskStatus.IMPL_REVIEW.value)

    impl = await db_session.scalar(
        select(TaskImplementation).where(TaskImplementation.task_id == task_id)
    )
    assert impl is not None
    assert impl.pr_url == "https://github.com/a/b/pull/1"
    assert impl.commit_sha == "deadbeef"


# ---------------------------------------------------------------------------
# Invariant 3 (deferred): every transition fires an effector or is exempt
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="Deferred to T-172 follow-on: requires T-162 (GitHub effector) + "
    "T-171 (startup validator + transition catalog) to be in place first."
)
async def test_every_transition_fires_an_effector() -> None:
    """Placeholder.

    Once T-162 + T-171 land, this asserts that the ``effector_call`` trace
    stream has an entry for every transition in the declared workflows,
    or the transition is in ``iter_no_effector_exemptions()``.
    """


# ---------------------------------------------------------------------------
# Engine-absent fallback — runs in the default suite
# ---------------------------------------------------------------------------


async def test_engine_absent_fallback_writes_inline(
    app: FastAPI,
    client: AsyncClient,
    api_key: str,
    db_session: AsyncSession,
) -> None:
    # No engine dependency override — get_lifecycle_engine_client returns
    # None by default. The signal adapter falls back to inline aux + inline
    # status writes, with no PendingAuxWrite rows enqueued.
    r = await client.post(
        "/api/v1/work-items",
        json={"externalRef": "FEAT-R3", "type": "FEAT", "title": "fallback"},
        headers=_h(api_key),
    )
    assert r.status_code == 202, r.text
    wi_id = uuid.UUID(r.json()["data"]["id"])

    task = await _seed_task(db_session, work_item_id=wi_id, ref="T-R3-a")
    task_id = task.id

    r = await client.post(
        f"/api/v1/tasks/{task_id}/approve", json={}, headers=_h(api_key)
    )
    assert r.status_code == 202, r.text

    # Approval landed inline — no outbox, no reactor needed.
    approval = await db_session.scalar(
        select(Approval).where(Approval.task_id == task_id)
    )
    assert approval is not None
    assert approval.stage == "proposed"
    assert approval.decision == "approve"

    orphan = await db_session.scalar(
        select(PendingAuxWrite).where(PendingAuxWrite.entity_id == task_id)
    )
    assert orphan is None, (
        "engine-absent fallback must not enqueue outbox rows"
    )

    # Status cache written inline by the transition helper.
    fresh = await db_session.scalar(select(Task).where(Task.id == task_id))
    assert fresh is not None
    await db_session.refresh(fresh)
    assert fresh.status == TaskStatus.ASSIGNING.value
