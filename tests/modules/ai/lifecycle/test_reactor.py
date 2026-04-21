"""Tests for the engine webhook reactor (FEAT-006 rc2 / T-130)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.webhook_auth import sign_body
from app.modules.ai.enums import TaskStatus, WorkItemStatus
from app.modules.ai.lifecycle import declarations, reactor
from app.modules.ai.models import PendingSignalContext, Task, WebhookEvent, WorkItem

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _build_event(
    *,
    item_id: uuid.UUID,
    workflow_id: uuid.UUID,
    from_status: str | None,
    to_status: str,
) -> reactor.LifecycleWebhookEvent:
    return reactor.LifecycleWebhookEvent(
        delivery_id=uuid.uuid4(),
        event_type="item.transitioned",
        tenant_id=uuid.uuid4(),
        workflow_id=workflow_id,
        item_id=item_id,
        timestamp=datetime.now(UTC),
        data=reactor.LifecycleWebhookData(
            from_status=from_status, to_status=to_status, triggered_by="engine"
        ),
    )


async def _seed_work_item(
    db: AsyncSession, *, engine_item_id: uuid.UUID, status: WorkItemStatus
) -> WorkItem:
    wi = WorkItem(
        external_ref=f"FEAT-{uuid.uuid4().hex[:6]}",
        type="FEAT",
        title="t",
        status=status.value,
        opened_by="admin",
        engine_item_id=engine_item_id,
    )
    db.add(wi)
    await db.commit()
    await db.refresh(wi)
    return wi


async def _seed_task(
    db: AsyncSession,
    *,
    wi_id: uuid.UUID,
    engine_item_id: uuid.UUID,
    status: TaskStatus,
    ref: str = "T-1",
) -> Task:
    t = Task(
        work_item_id=wi_id,
        external_ref=ref,
        title="do",
        status=status.value,
        proposer_type="admin",
        proposer_id="admin",
        engine_item_id=engine_item_id,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


class TestHandleTransition:
    async def test_task_approved_fires_w2(self, db_session: AsyncSession) -> None:
        wi_engine_id = uuid.uuid4()
        task_engine_id = uuid.uuid4()
        wi = await _seed_work_item(
            db_session, engine_item_id=wi_engine_id, status=WorkItemStatus.OPEN
        )
        await _seed_task(
            db_session,
            wi_id=wi.id,
            engine_item_id=task_engine_id,
            status=TaskStatus.APPROVED,
            ref="T-W2",
        )

        task_workflow_id = uuid.uuid4()
        event = _build_event(
            item_id=task_engine_id,
            workflow_id=task_workflow_id,
            from_status="proposed",
            to_status="approved",
        )
        mapping = {task_workflow_id: declarations.TASK_WORKFLOW_NAME}
        await reactor.handle_transition(db_session, event, workflow_name_by_id=mapping)
        await db_session.commit()

        await db_session.refresh(wi)
        assert wi.status == WorkItemStatus.IN_PROGRESS.value

    async def test_task_done_fires_w5_when_last_terminal(
        self, db_session: AsyncSession
    ) -> None:
        wi_engine_id = uuid.uuid4()
        task_engine_id = uuid.uuid4()
        wi = await _seed_work_item(
            db_session, engine_item_id=wi_engine_id, status=WorkItemStatus.IN_PROGRESS
        )
        await _seed_task(
            db_session,
            wi_id=wi.id,
            engine_item_id=task_engine_id,
            status=TaskStatus.DONE,
            ref="T-W5",
        )

        task_workflow_id = uuid.uuid4()
        event = _build_event(
            item_id=task_engine_id,
            workflow_id=task_workflow_id,
            from_status="impl_review",
            to_status="done",
        )
        mapping = {task_workflow_id: declarations.TASK_WORKFLOW_NAME}
        await reactor.handle_transition(db_session, event, workflow_name_by_id=mapping)
        await db_session.commit()

        await db_session.refresh(wi)
        assert wi.status == WorkItemStatus.READY.value

    async def test_unknown_item_logs_and_returns(
        self, db_session: AsyncSession
    ) -> None:
        event = _build_event(
            item_id=uuid.uuid4(),
            workflow_id=uuid.uuid4(),
            from_status="proposed",
            to_status="approved",
        )
        # Should not raise.
        await reactor.handle_transition(db_session, event)

    async def test_work_item_transition_no_derivation(
        self, db_session: AsyncSession
    ) -> None:
        wi_engine_id = uuid.uuid4()
        wi = await _seed_work_item(
            db_session,
            engine_item_id=wi_engine_id,
            status=WorkItemStatus.IN_PROGRESS,
        )
        wi_workflow_id = uuid.uuid4()
        event = _build_event(
            item_id=wi_engine_id,
            workflow_id=wi_workflow_id,
            from_status="in_progress",
            to_status="locked",
        )
        mapping = {wi_workflow_id: declarations.WORK_ITEM_WORKFLOW_NAME}
        await reactor.handle_transition(db_session, event, workflow_name_by_id=mapping)
        # FEAT-008/T-169: the reactor now updates the status cache from the
        # engine's authoritative to_status, even for work items (no
        # derivation fired, but the cache converges).
        await db_session.refresh(wi)
        assert wi.status == "locked"


class TestCorrelationConsumption:
    async def test_reactor_deletes_matching_context_row(
        self, db_session: AsyncSession
    ) -> None:
        wi_engine_id = uuid.uuid4()
        task_engine_id = uuid.uuid4()
        wi = await _seed_work_item(
            db_session, engine_item_id=wi_engine_id, status=WorkItemStatus.IN_PROGRESS
        )
        await _seed_task(
            db_session,
            wi_id=wi.id,
            engine_item_id=task_engine_id,
            status=TaskStatus.IMPL_REVIEW,
            ref="T-CORR",
        )

        corr = uuid.uuid4()
        db_session.add(
            PendingSignalContext(
                correlation_id=corr,
                signal_name="approve-review",
                payload={"taskId": "T-CORR", "actorRole": "admin"},
            )
        )
        await db_session.commit()

        task_workflow_id = uuid.uuid4()
        event = reactor.LifecycleWebhookEvent(
            delivery_id=uuid.uuid4(),
            event_type="item.transitioned",
            tenant_id=uuid.uuid4(),
            workflow_id=task_workflow_id,
            item_id=task_engine_id,
            timestamp=datetime.now(UTC),
            data=reactor.LifecycleWebhookData(
                from_status="impl_review",
                to_status="done",
                triggered_by=f"user:admin orchestrator-corr:{corr}",
            ),
        )
        mapping = {task_workflow_id: declarations.TASK_WORKFLOW_NAME}
        await reactor.handle_transition(
            db_session, event, workflow_name_by_id=mapping
        )
        await db_session.commit()

        # Context row deleted after consumption.
        remaining = await db_session.scalar(
            select(PendingSignalContext).where(
                PendingSignalContext.correlation_id == corr
            )
        )
        assert remaining is None

    async def test_reactor_no_correlation_is_no_op(
        self, db_session: AsyncSession
    ) -> None:
        wi_engine_id = uuid.uuid4()
        task_engine_id = uuid.uuid4()
        wi = await _seed_work_item(
            db_session, engine_item_id=wi_engine_id, status=WorkItemStatus.IN_PROGRESS
        )
        await _seed_task(
            db_session,
            wi_id=wi.id,
            engine_item_id=task_engine_id,
            status=TaskStatus.DONE,
            ref="T-NC",
        )
        task_workflow_id = uuid.uuid4()
        event = reactor.LifecycleWebhookEvent(
            delivery_id=uuid.uuid4(),
            event_type="item.transitioned",
            tenant_id=uuid.uuid4(),
            workflow_id=task_workflow_id,
            item_id=task_engine_id,
            timestamp=datetime.now(UTC),
            data=reactor.LifecycleWebhookData(
                from_status="impl_review", to_status="done", triggered_by=None
            ),
        )
        mapping = {task_workflow_id: declarations.TASK_WORKFLOW_NAME}
        # Should not raise.
        await reactor.handle_transition(
            db_session, event, workflow_name_by_id=mapping
        )


class TestWebhookEndpoint:
    async def test_bad_signature_persists_and_returns_401(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        body = json.dumps(
            {
                "deliveryId": str(uuid.uuid4()),
                "eventType": "item.transitioned",
                "tenantId": str(uuid.uuid4()),
                "workflowId": str(uuid.uuid4()),
                "itemId": str(uuid.uuid4()),
                "timestamp": "2026-04-19T00:00:00Z",
                "data": {"fromStatus": "open", "toStatus": "closed", "triggeredBy": "e"},
            }
        ).encode()
        resp = await client.post(
            "/hooks/engine/lifecycle/item-transitioned",
            content=body,
            headers={
                "X-FlowEngine-Signature": "sha256=bogus",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 401
        rows = (
            await db_session.scalars(
                select(WebhookEvent).where(
                    WebhookEvent.event_type == "lifecycle_item_transitioned"
                )
            )
        ).all()
        assert any(r.signature_ok is False for r in rows)

    async def test_valid_signature_persists_and_acks(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        webhook_secret: str,
    ) -> None:
        body_dict = {
            "deliveryId": str(uuid.uuid4()),
            "eventType": "item.transitioned",
            "tenantId": str(uuid.uuid4()),
            "workflowId": str(uuid.uuid4()),
            "itemId": str(uuid.uuid4()),
            "timestamp": "2026-04-19T00:00:00Z",
            "data": {"fromStatus": "x", "toStatus": "y", "triggeredBy": "e"},
        }
        body = json.dumps(body_dict).encode()
        sig = sign_body(body, webhook_secret)
        resp = await client.post(
            "/hooks/engine/lifecycle/item-transitioned",
            content=body,
            headers={
                "X-FlowEngine-Signature": sig,
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 202, resp.text
        payload = resp.json()
        assert payload["data"]["received"] is True
