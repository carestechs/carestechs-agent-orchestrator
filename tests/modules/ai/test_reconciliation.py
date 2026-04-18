"""Tests for the webhook → step reconciliation helper (T-038)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import RunStatus, StepStatus, WebhookEventType
from app.modules.ai.models import Run, Step, WebhookEvent
from app.modules.ai.reconciliation import next_step_state
from app.modules.ai.service import _reconcile_step_from_event

# ---------------------------------------------------------------------------
# Pure function: next_step_state
# ---------------------------------------------------------------------------


class TestNextStepState:
    @pytest.mark.parametrize(
        ("current", "event", "expected_status", "expected_changed"),
        [
            # Forward transitions
            (StepStatus.PENDING, WebhookEventType.NODE_STARTED, StepStatus.IN_PROGRESS, True),
            (StepStatus.PENDING, WebhookEventType.NODE_FINISHED, StepStatus.COMPLETED, True),
            (StepStatus.PENDING, WebhookEventType.NODE_FAILED, StepStatus.FAILED, True),
            (StepStatus.DISPATCHED, WebhookEventType.NODE_STARTED, StepStatus.IN_PROGRESS, True),
            (StepStatus.DISPATCHED, WebhookEventType.NODE_FINISHED, StepStatus.COMPLETED, True),
            (StepStatus.IN_PROGRESS, WebhookEventType.NODE_FINISHED, StepStatus.COMPLETED, True),
            (StepStatus.IN_PROGRESS, WebhookEventType.NODE_FAILED, StepStatus.FAILED, True),
            # Rollback attempts — rejected
            (StepStatus.COMPLETED, WebhookEventType.NODE_STARTED, StepStatus.COMPLETED, False),
            (StepStatus.FAILED, WebhookEventType.NODE_STARTED, StepStatus.FAILED, False),
            (StepStatus.IN_PROGRESS, WebhookEventType.NODE_STARTED, StepStatus.IN_PROGRESS, False),
            # Run-level events — no step transition
            (StepStatus.PENDING, WebhookEventType.FLOW_TERMINATED, StepStatus.PENDING, False),
            (StepStatus.COMPLETED, WebhookEventType.FLOW_TERMINATED, StepStatus.COMPLETED, False),
        ],
    )
    def test_transition(
        self,
        current: StepStatus,
        event: WebhookEventType,
        expected_status: StepStatus,
        expected_changed: bool,
    ) -> None:
        new_status, changed = next_step_state(current, event)
        assert new_status == expected_status
        assert changed == expected_changed

    @pytest.mark.parametrize(
        "current", list(StepStatus), ids=[s.value for s in StepStatus]
    )
    @pytest.mark.parametrize(
        "event", list(WebhookEventType), ids=[e.value for e in WebhookEventType]
    )
    def test_full_matrix_is_monotonic(
        self, current: StepStatus, event: WebhookEventType
    ) -> None:
        """Systematic 5x4 = 20 combinations — every transition is either a
        forward rank jump (``changed=True``) or a no-op (``changed=False``).
        A backwards transition here would be a regression."""
        from app.modules.ai.reconciliation import _STATUS_RANK

        new_status, changed = next_step_state(current, event)
        # Rank never decreases, regardless of the event.
        assert _STATUS_RANK[new_status] >= _STATUS_RANK[current]
        if changed:
            # Only forward transitions advertise changed=True.
            assert _STATUS_RANK[new_status] > _STATUS_RANK[current]
        else:
            # Unchanged branch — status must be identical to input.
            assert new_status == current


# ---------------------------------------------------------------------------
# Service helper: _reconcile_step_from_event (integration with real DB)
# ---------------------------------------------------------------------------


async def _seed_run_and_step(
    db: AsyncSession, engine_run_id: str, run_status: RunStatus = RunStatus.RUNNING
) -> tuple[Run, Step]:
    run = Run(
        agent_ref="test@1",
        agent_definition_hash="sha256:" + "a" * 64,
        intake={"brief": "x"},
        status=run_status,
        started_at=datetime.now(UTC),
        trace_uri="file:///tmp/trace.jsonl",
    )
    db.add(run)
    await db.flush()
    step = Step(
        run_id=run.id,
        step_number=1,
        node_name="analyze_brief",
        node_inputs={},
        engine_run_id=engine_run_id,
        status=StepStatus.DISPATCHED,
    )
    db.add(step)
    await db.flush()
    return run, step


def _event(step: Step, event_type: WebhookEventType, payload: dict | None = None) -> WebhookEvent:
    return WebhookEvent(
        run_id=step.run_id,
        step_id=step.id,
        event_type=event_type.value,
        engine_run_id=step.engine_run_id or "",
        payload=payload or {},
        signature_ok=True,
        dedupe_key=f"evt-{event_type.value}",
    )


class TestReconcileStep:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_node_finished_marks_step_completed(
        self, db_session: AsyncSession
    ) -> None:
        _run, step = await _seed_run_and_step(db_session, "eng-1")
        event = _event(step, WebhookEventType.NODE_FINISHED, payload={"result": {"ok": True}})
        db_session.add(event)
        await db_session.commit()

        changed = await _reconcile_step_from_event(event, db_session)
        assert changed is True

        refreshed = await db_session.scalar(select(Step).where(Step.id == step.id))
        assert refreshed is not None
        assert refreshed.status == StepStatus.COMPLETED
        assert refreshed.node_result == {"ok": True}
        assert refreshed.completed_at is not None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_node_failed_records_error(self, db_session: AsyncSession) -> None:
        _run, step = await _seed_run_and_step(db_session, "eng-2")
        event = _event(
            step,
            WebhookEventType.NODE_FAILED,
            payload={"error": {"code": "timeout", "message": "slow"}},
        )
        db_session.add(event)
        await db_session.commit()

        await _reconcile_step_from_event(event, db_session)

        refreshed = await db_session.scalar(select(Step).where(Step.id == step.id))
        assert refreshed is not None
        assert refreshed.status == StepStatus.FAILED
        assert refreshed.error == {"code": "timeout", "message": "slow"}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_late_event_for_terminal_run_skipped(
        self, db_session: AsyncSession
    ) -> None:
        _run, step = await _seed_run_and_step(
            db_session, "eng-3", run_status=RunStatus.CANCELLED
        )
        event = _event(step, WebhookEventType.NODE_FINISHED, payload={"result": {}})
        db_session.add(event)
        await db_session.commit()

        changed = await _reconcile_step_from_event(event, db_session)
        assert changed is False

        refreshed = await db_session.scalar(select(Step).where(Step.id == step.id))
        assert refreshed is not None
        assert refreshed.status == StepStatus.DISPATCHED  # unchanged

    @pytest.mark.asyncio(loop_scope="function")
    async def test_rollback_attempt_ignored(self, db_session: AsyncSession) -> None:
        _run, step = await _seed_run_and_step(db_session, "eng-4")
        step.status = StepStatus.COMPLETED
        await db_session.flush()
        event = _event(step, WebhookEventType.NODE_STARTED)
        db_session.add(event)
        await db_session.commit()

        changed = await _reconcile_step_from_event(event, db_session)
        assert changed is False


# ---------------------------------------------------------------------------
# Integration: full ingest_engine_event with a fake supervisor
# ---------------------------------------------------------------------------


class _FakeSupervisor:
    def __init__(self) -> None:
        self.woken: list = []

    async def wake(self, run_id) -> None:  # type: ignore[no-untyped-def]
        self.woken.append(run_id)


class _FakeTrace:
    def __init__(self) -> None:
        self.events: list = []

    async def record_step(self, run_id, step) -> None:  # type: ignore[no-untyped-def]
        pass

    async def record_policy_call(self, run_id, call) -> None:  # type: ignore[no-untyped-def]
        pass

    async def record_webhook_event(self, run_id, event) -> None:  # type: ignore[no-untyped-def]
        self.events.append((run_id, event))

    async def open_run_stream(self, run_id):  # type: ignore[no-untyped-def]
        return _empty()


async def _empty():
    return
    yield


class TestIngestEngineEventWiring:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_wake_called_when_event_reconciles(
        self, db_session: AsyncSession
    ) -> None:
        from app.modules.ai.service import ingest_engine_event

        _run, step = await _seed_run_and_step(db_session, "eng-5")

        supervisor = _FakeSupervisor()
        trace = _FakeTrace()
        body = {
            "event_type": "node_finished",
            "engine_run_id": "eng-5",
            "engine_event_id": "evt-ok-1",
            "payload": {"result": {"ok": True}},
        }

        await ingest_engine_event(body, True, db_session, supervisor=supervisor, trace=trace)

        assert supervisor.woken == [step.run_id]
        assert len(trace.events) == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_bad_signature_skips_wake_and_reconciliation(
        self, db_session: AsyncSession
    ) -> None:
        from app.modules.ai.service import ingest_engine_event

        _run, step = await _seed_run_and_step(db_session, "eng-6")

        supervisor = _FakeSupervisor()
        trace = _FakeTrace()
        body = {
            "event_type": "node_finished",
            "engine_run_id": "eng-6",
            "engine_event_id": "evt-badsig",
            "payload": {"result": {}},
        }

        await ingest_engine_event(body, False, db_session, supervisor=supervisor, trace=trace)

        assert supervisor.woken == []
        # Step should NOT have advanced despite the event being a NODE_FINISHED
        refreshed = await db_session.scalar(select(Step).where(Step.id == step.id))
        assert refreshed is not None
        assert refreshed.status == StepStatus.DISPATCHED
        # Trace still records the event for forensics
        assert len(trace.events) == 1
