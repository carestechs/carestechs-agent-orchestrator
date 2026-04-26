"""Reactor wake-dispatch step (FEAT-010 / T-233).

Asserts the new ``_wake_dispatch`` leg of the reactor pipeline:

1. Pipeline order is the canonical ``materialize aux → consume
   correlation → fire effectors → wake dispatch → fire derivations``.
2. Wake-on-match: a ``DISPATCHED`` engine dispatch with matching
   correlation id is marked ``COMPLETED`` and ``deliver_dispatch``
   fires.
3. No-match: webhook arrives with no dispatch row carrying the
   correlation (race or non-executor-driven transition) — wake step
   no-ops, no exception.
4. Already-terminal: dispatch row is already ``COMPLETED`` — wake step
   no-ops; ``deliver_dispatch`` is *not* re-called.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import DispatchMode, DispatchOutcome, DispatchState, RunStatus, StepStatus
from app.modules.ai.lifecycle import declarations, reactor
from app.modules.ai.models import (
    Dispatch,
    Run,
    Step,
    Task,
    WorkItem,
    generate_uuid7,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _build_event(
    *,
    item_id: uuid.UUID,
    workflow_id: uuid.UUID,
    correlation_id: uuid.UUID | None,
    from_status: str = "in_progress",
    to_status: str = "ready",
) -> reactor.LifecycleWebhookEvent:
    triggered_by = f"orchestrator-corr:{correlation_id}" if correlation_id is not None else "engine"
    return reactor.LifecycleWebhookEvent(
        delivery_id=uuid.uuid4(),
        event_type="item.transitioned",
        tenant_id=uuid.uuid4(),
        workflow_id=workflow_id,
        item_id=item_id,
        timestamp=datetime.now(UTC),
        data=reactor.LifecycleWebhookData(
            from_status=from_status,
            to_status=to_status,
            triggered_by=triggered_by,
        ),
    )


async def _seed_run(db: AsyncSession) -> Run:
    run = Run(
        agent_ref="test-engine-agent@0.1.0",
        agent_definition_hash="sha256:" + "0" * 64,
        intake={},
        status=RunStatus.RUNNING,
        started_at=datetime.now(UTC),
        trace_uri="file:///tmp/t.jsonl",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def _seed_step(db: AsyncSession, *, run_id: uuid.UUID) -> Step:
    step = Step(
        id=generate_uuid7(),
        run_id=run_id,
        step_number=1,
        node_name="advance_engine",
        node_inputs={},
        status=StepStatus.IN_PROGRESS,
    )
    db.add(step)
    await db.commit()
    await db.refresh(step)
    return step


async def _seed_dispatch(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    step_id: uuid.UUID,
    correlation_id: uuid.UUID,
    state: DispatchState = DispatchState.DISPATCHED,
    transition_key: str = "work_item.W2",
) -> Dispatch:
    intake: dict[str, Any] = {
        "correlation_id": str(correlation_id),
        "transition_key": transition_key,
        "engineItemId": str(uuid.uuid4()),
    }
    now = datetime.now(UTC)
    d = Dispatch(
        dispatch_id=generate_uuid7(),
        step_id=step_id,
        run_id=run_id,
        executor_ref="engine:work_item.W2",
        mode=DispatchMode.ENGINE.value,
        state=state.value,
        intake=intake,
        started_at=now,
        dispatched_at=now if state != DispatchState.PENDING else None,
        finished_at=now if state in (DispatchState.COMPLETED, DispatchState.FAILED) else None,
        outcome=DispatchOutcome.OK.value if state == DispatchState.COMPLETED else None,
    )
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d


async def _seed_work_item(db: AsyncSession, *, engine_item_id: uuid.UUID) -> WorkItem:
    wi = WorkItem(
        external_ref=f"FEAT-{uuid.uuid4().hex[:6]}",
        type="FEAT",
        title="t",
        status="in_progress",
        opened_by="admin",
        engine_item_id=engine_item_id,
    )
    db.add(wi)
    await db.commit()
    await db.refresh(wi)
    return wi


class TestWakeDispatch:
    async def test_wake_on_match(self, db_session: AsyncSession) -> None:
        run = await _seed_run(db_session)
        step = await _seed_step(db_session, run_id=run.id)
        correlation_id = uuid.uuid4()
        dispatch = await _seed_dispatch(
            db_session,
            run_id=run.id,
            step_id=step.id,
            correlation_id=correlation_id,
        )
        engine_item_id = uuid.uuid4()
        wi = await _seed_work_item(db_session, engine_item_id=engine_item_id)
        del wi  # only the dispatch lookup matters

        wf_id = uuid.uuid4()
        event = _build_event(
            item_id=engine_item_id,
            workflow_id=wf_id,
            correlation_id=correlation_id,
        )
        mapping = {wf_id: declarations.WORK_ITEM_WORKFLOW_NAME}

        supervisor = MagicMock()
        await reactor.handle_transition(
            db_session,
            event,
            workflow_name_by_id=mapping,
            supervisor=supervisor,
        )
        await db_session.commit()

        # Dispatch flipped to completed.
        fresh = await db_session.scalar(select(Dispatch).where(Dispatch.dispatch_id == dispatch.dispatch_id))
        assert fresh is not None
        assert fresh.state == DispatchState.COMPLETED.value
        assert fresh.outcome == DispatchOutcome.OK.value
        assert fresh.result is not None
        assert fresh.result["correlation_id"] == str(correlation_id)
        assert fresh.result["transition_key"] == "work_item.W2"
        assert fresh.result["engine_to_status"] == "ready"

        # Supervisor was woken with an envelope carrying engine metadata.
        assert supervisor.deliver_dispatch.called
        args, _ = supervisor.deliver_dispatch.call_args
        assert args[0] == dispatch.dispatch_id
        envelope = args[1]
        assert envelope.mode == DispatchMode.ENGINE
        assert envelope.state == DispatchState.COMPLETED
        assert envelope.correlation_id == correlation_id
        assert envelope.transition_key == "work_item.W2"

    async def test_no_match_is_noop(self, db_session: AsyncSession) -> None:
        """Webhook arrives before dispatch row commits — wake no-ops."""
        engine_item_id = uuid.uuid4()
        wi = await _seed_work_item(db_session, engine_item_id=engine_item_id)
        del wi

        wf_id = uuid.uuid4()
        unmatched_correlation = uuid.uuid4()
        event = _build_event(
            item_id=engine_item_id,
            workflow_id=wf_id,
            correlation_id=unmatched_correlation,
        )
        mapping = {wf_id: declarations.WORK_ITEM_WORKFLOW_NAME}

        supervisor = MagicMock()
        # Must not raise; deliver_dispatch must not be called.
        await reactor.handle_transition(
            db_session,
            event,
            workflow_name_by_id=mapping,
            supervisor=supervisor,
        )
        await db_session.commit()

        assert not supervisor.deliver_dispatch.called

    async def test_already_terminal_is_noop(self, db_session: AsyncSession) -> None:
        run = await _seed_run(db_session)
        step = await _seed_step(db_session, run_id=run.id)
        correlation_id = uuid.uuid4()
        dispatch = await _seed_dispatch(
            db_session,
            run_id=run.id,
            step_id=step.id,
            correlation_id=correlation_id,
            state=DispatchState.COMPLETED,
        )
        engine_item_id = uuid.uuid4()
        wi = await _seed_work_item(db_session, engine_item_id=engine_item_id)
        del wi

        wf_id = uuid.uuid4()
        event = _build_event(
            item_id=engine_item_id,
            workflow_id=wf_id,
            correlation_id=correlation_id,
        )
        mapping = {wf_id: declarations.WORK_ITEM_WORKFLOW_NAME}

        supervisor = MagicMock()
        await reactor.handle_transition(
            db_session,
            event,
            workflow_name_by_id=mapping,
            supervisor=supervisor,
        )
        await db_session.commit()

        # Replayed webhook: dispatch row remained terminal; supervisor not called.
        fresh = await db_session.scalar(select(Dispatch).where(Dispatch.dispatch_id == dispatch.dispatch_id))
        assert fresh is not None
        assert fresh.state == DispatchState.COMPLETED.value
        assert not supervisor.deliver_dispatch.called

    async def test_supervisor_none_skips_wake(self, db_session: AsyncSession) -> None:
        """Test fixtures often pass ``supervisor=None``; wake step is bypassed."""
        run = await _seed_run(db_session)
        step = await _seed_step(db_session, run_id=run.id)
        correlation_id = uuid.uuid4()
        dispatch = await _seed_dispatch(
            db_session,
            run_id=run.id,
            step_id=step.id,
            correlation_id=correlation_id,
        )
        engine_item_id = uuid.uuid4()
        wi = await _seed_work_item(db_session, engine_item_id=engine_item_id)
        del wi

        wf_id = uuid.uuid4()
        event = _build_event(
            item_id=engine_item_id,
            workflow_id=wf_id,
            correlation_id=correlation_id,
        )
        mapping = {wf_id: declarations.WORK_ITEM_WORKFLOW_NAME}

        # No supervisor argument → wake step is skipped.
        await reactor.handle_transition(
            db_session,
            event,
            workflow_name_by_id=mapping,
        )
        await db_session.commit()

        fresh = await db_session.scalar(select(Dispatch).where(Dispatch.dispatch_id == dispatch.dispatch_id))
        assert fresh is not None
        # Dispatch remained dispatched; reactor without supervisor doesn't wake.
        assert fresh.state == DispatchState.DISPATCHED.value


class TestPipelineOrder:
    async def test_call_order_aux_corr_effectors_wake_derivations(
        self, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        """Mocks every pipeline step and asserts the call sequence.

        The canonical order is the contract from the FEAT-010 brief:

            materialize aux → (status cache) → consume correlation
            → fire effectors → wake dispatch → fire derivations
        """
        calls: list[str] = []

        async def fake_materialize(db: Any, corr: uuid.UUID) -> None:
            del db, corr
            calls.append("materialize_aux")

        async def fake_status(db: Any, name: str, evt: Any) -> None:
            del db, name, evt
            calls.append("update_status_cache")

        async def fake_consume(db: Any, triggered_by: Any) -> None:
            del db, triggered_by
            calls.append("consume_correlation")

        async def fake_effectors(*args: Any, **kwargs: Any) -> None:
            del args, kwargs
            calls.append("dispatch_effectors")

        async def fake_wake(db: Any, corr: uuid.UUID, evt: Any, sup: Any) -> None:
            del db, corr, evt, sup
            calls.append("wake_dispatch")

        async def fake_handle_task_transition(db: Any, item_id: uuid.UUID, to_status: Any) -> None:
            del db, item_id, to_status
            calls.append("derivations")

        monkeypatch.setattr(reactor, "_materialize_aux", fake_materialize)
        monkeypatch.setattr(reactor, "_update_status_cache", fake_status)
        monkeypatch.setattr(reactor, "_consume_correlation", fake_consume)
        monkeypatch.setattr(reactor, "_dispatch_effectors", fake_effectors)
        monkeypatch.setattr(reactor, "_wake_dispatch", fake_wake)
        monkeypatch.setattr(reactor, "_handle_task_transition", fake_handle_task_transition)

        # Seed a Task so the workflow_name resolves and the derivation
        # branch is reachable.
        engine_item_id = uuid.uuid4()
        wi = WorkItem(
            external_ref=f"FEAT-{uuid.uuid4().hex[:6]}",
            type="FEAT",
            title="t",
            status="in_progress",
            opened_by="admin",
        )
        db_session.add(wi)
        await db_session.flush()
        t = Task(
            work_item_id=wi.id,
            external_ref="T-ORDER",
            title="o",
            status="approved",
            proposer_type="admin",
            proposer_id="admin",
            engine_item_id=engine_item_id,
        )
        db_session.add(t)
        await db_session.commit()

        wf_id = uuid.uuid4()
        correlation_id = uuid.uuid4()
        event = _build_event(
            item_id=engine_item_id,
            workflow_id=wf_id,
            correlation_id=correlation_id,
            from_status="proposed",
            to_status="approved",
        )
        mapping = {wf_id: declarations.TASK_WORKFLOW_NAME}

        supervisor = MagicMock()
        # ``registry`` + ``settings`` are not None → effectors leg fires.
        registry = MagicMock()
        settings = MagicMock()

        await reactor.handle_transition(
            db_session,
            event,
            workflow_name_by_id=mapping,
            registry=registry,
            settings=settings,
            supervisor=supervisor,
        )

        # Canonical order: materialize → status_cache → consume_corr →
        # effectors → wake → derivations. (status_cache may run earlier;
        # the load-bearing constraint is wake-after-effectors and
        # wake-before-derivations.)
        assert calls.index("materialize_aux") < calls.index("dispatch_effectors")
        assert calls.index("consume_correlation") < calls.index("dispatch_effectors")
        assert calls.index("dispatch_effectors") < calls.index("wake_dispatch")
        assert calls.index("wake_dispatch") < calls.index("derivations")
