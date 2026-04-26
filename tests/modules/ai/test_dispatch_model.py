"""Unit tests for the Dispatch model + state machine (FEAT-009 / T-212)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import (
    DispatchMode,
    DispatchOutcome,
    DispatchState,
    RunStatus,
    StepStatus,
)
from app.modules.ai.models import (
    Dispatch,
    IllegalDispatchTransition,
    Run,
    Step,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _now() -> datetime:
    return datetime.now(UTC)


async def _seed_run_and_step(db: AsyncSession) -> tuple[Run, Step]:
    run = Run(
        agent_ref="lifecycle-agent@0.2.0",
        agent_definition_hash="sha256:" + "0" * 64,
        intake={},
        status=RunStatus.RUNNING,
        started_at=_now(),
        trace_uri="file:///tmp/t.jsonl",
    )
    db.add(run)
    await db.flush()
    step = Step(
        run_id=run.id,
        step_number=1,
        node_name="request_task_generation",
        node_inputs={},
        status=StepStatus.PENDING,
    )
    db.add(step)
    await db.flush()
    return run, step


def _make_dispatch(run: Run, step: Step) -> Dispatch:
    # ``state`` is set explicitly so the in-memory state machine works
    # without first flushing — the column-level default only fires on INSERT.
    return Dispatch(
        step_id=step.id,
        run_id=run.id,
        executor_ref="local:request_task_generation",
        mode=DispatchMode.LOCAL,
        state=DispatchState.PENDING,
        intake={"hello": "world"},
    )


class TestStateMachine:
    async def test_pending_to_dispatched_to_completed(self, db_session: AsyncSession) -> None:
        run, step = await _seed_run_and_step(db_session)
        dispatch = _make_dispatch(run, step)
        db_session.add(dispatch)
        await db_session.flush()

        assert dispatch.state == DispatchState.PENDING
        assert dispatch.outcome is None

        dispatch.mark_dispatched(at=_now())
        assert dispatch.state == DispatchState.DISPATCHED
        assert dispatch.dispatched_at is not None

        finished = _now()
        dispatch.mark_completed(at=finished, result={"verdict": "pass"}, detail="ok")
        assert dispatch.state == DispatchState.COMPLETED
        assert dispatch.outcome == DispatchOutcome.OK
        assert dispatch.result == {"verdict": "pass"}
        assert dispatch.finished_at == finished

        await db_session.commit()
        # Round-trip from DB.
        reloaded = await db_session.scalar(select(Dispatch).where(Dispatch.dispatch_id == dispatch.dispatch_id))
        assert reloaded is not None
        assert reloaded.state == DispatchState.COMPLETED
        assert reloaded.outcome == DispatchOutcome.OK

    async def test_pending_to_dispatched_to_failed(self, db_session: AsyncSession) -> None:
        run, step = await _seed_run_and_step(db_session)
        dispatch = _make_dispatch(run, step)
        db_session.add(dispatch)
        await db_session.flush()

        dispatch.mark_dispatched(at=_now())
        dispatch.mark_failed(at=_now(), result={"reason": "timeout"}, detail="exceeded 600s")

        assert dispatch.state == DispatchState.FAILED
        assert dispatch.outcome == DispatchOutcome.ERROR
        assert dispatch.detail == "exceeded 600s"

    async def test_pending_to_cancelled_directly(self, db_session: AsyncSession) -> None:
        """Cancellation that arrives between row insert and the executor call."""
        run, step = await _seed_run_and_step(db_session)
        dispatch = _make_dispatch(run, step)
        db_session.add(dispatch)
        await db_session.flush()

        dispatch.mark_cancelled(at=_now(), detail="run cancelled by operator")
        assert dispatch.state == DispatchState.CANCELLED
        assert dispatch.outcome == DispatchOutcome.CANCELLED

    async def test_dispatched_to_cancelled(self, db_session: AsyncSession) -> None:
        run, step = await _seed_run_and_step(db_session)
        dispatch = _make_dispatch(run, step)
        db_session.add(dispatch)
        await db_session.flush()

        dispatch.mark_dispatched(at=_now())
        dispatch.mark_cancelled(at=_now())
        assert dispatch.state == DispatchState.CANCELLED


class TestIllegalTransitions:
    async def test_pending_directly_to_completed_raises(self, db_session: AsyncSession) -> None:
        run, step = await _seed_run_and_step(db_session)
        dispatch = _make_dispatch(run, step)
        with pytest.raises(IllegalDispatchTransition):
            dispatch.mark_completed(at=_now())

    async def test_pending_directly_to_failed_raises(self, db_session: AsyncSession) -> None:
        run, step = await _seed_run_and_step(db_session)
        dispatch = _make_dispatch(run, step)
        with pytest.raises(IllegalDispatchTransition):
            dispatch.mark_failed(at=_now())

    async def test_completed_cannot_transition(self, db_session: AsyncSession) -> None:
        run, step = await _seed_run_and_step(db_session)
        dispatch = _make_dispatch(run, step)
        dispatch.mark_dispatched(at=_now())
        dispatch.mark_completed(at=_now())

        for op in (dispatch.mark_failed, dispatch.mark_cancelled):
            with pytest.raises(IllegalDispatchTransition):
                op(at=_now())

    async def test_failed_cannot_transition(self, db_session: AsyncSession) -> None:
        run, step = await _seed_run_and_step(db_session)
        dispatch = _make_dispatch(run, step)
        dispatch.mark_dispatched(at=_now())
        dispatch.mark_failed(at=_now())

        with pytest.raises(IllegalDispatchTransition):
            dispatch.mark_completed(at=_now())


class TestUniqueConstraints:
    async def test_one_dispatch_per_step(self, db_session: AsyncSession) -> None:
        run, step = await _seed_run_and_step(db_session)
        d1 = _make_dispatch(run, step)
        db_session.add(d1)
        await db_session.flush()

        d2 = _make_dispatch(run, step)  # same step_id
        db_session.add(d2)
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestDtoRoundTrip:
    async def test_serializes_to_dispatch_envelope(self, db_session: AsyncSession) -> None:
        from app.modules.ai.schemas import DispatchEnvelope

        run, step = await _seed_run_and_step(db_session)
        dispatch = _make_dispatch(run, step)
        db_session.add(dispatch)
        await db_session.flush()
        dispatch.mark_dispatched(at=_now())
        dispatch.mark_completed(at=_now(), result={"ok": True})

        envelope = DispatchEnvelope.model_validate(dispatch, from_attributes=True)
        assert envelope.dispatch_id == dispatch.dispatch_id
        assert envelope.state == DispatchState.COMPLETED
        assert envelope.outcome == DispatchOutcome.OK
        # camelCase JSON output
        dumped = envelope.model_dump(by_alias=True)
        assert "dispatchId" in dumped
        assert "executorRef" in dumped


async def test_dispatch_id_column_has_default_factory() -> None:
    # default factory only fires on flush; we just verify the column carries
    # the UUIDv7 default callable so PK auto-population is wired.
    Dispatch(
        step_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        executor_ref="x",
        mode=DispatchMode.LOCAL,
        intake={},
    )
    assert Dispatch.__table__.c.dispatch_id.default is not None
