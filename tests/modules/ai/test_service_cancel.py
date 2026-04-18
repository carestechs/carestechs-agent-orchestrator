"""Service-layer tests for ``cancel_run`` (T-042)."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.modules.ai.enums import RunStatus, StopReason
from app.modules.ai.models import Run, RunMemory
from app.modules.ai.schemas import CancelRunRequest
from app.modules.ai.service import cancel_run


class _FakeSupervisor:
    """Records cancels; runs no task."""

    def __init__(self) -> None:
        self.cancelled: list[uuid.UUID] = []

    async def cancel(self, run_id: uuid.UUID) -> None:
        self.cancelled.append(run_id)


async def _seed_run(db: AsyncSession, *, status: RunStatus) -> Run:
    run = Run(
        agent_ref="a",
        agent_definition_hash="sha256:" + "0" * 64,
        intake={},
        status=status,
        started_at=datetime.now(UTC),
        trace_uri="file:///tmp/t.jsonl",
    )
    db.add(run)
    await db.flush()
    db.add(RunMemory(run_id=run.id, data={}))
    await db.commit()
    return run


class TestCancelRun:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_cancels_pending_run_and_returns_terminal_dto(
        self, db_session: AsyncSession
    ) -> None:
        run = await _seed_run(db_session, status=RunStatus.PENDING)
        supervisor = _FakeSupervisor()

        start = time.monotonic()
        summary = await cancel_run(
            run.id,
            CancelRunRequest(reason="operator abort"),
            db_session,
            supervisor=supervisor,  # type: ignore[arg-type]
        )
        elapsed_ms = (time.monotonic() - start) * 1000

        assert elapsed_ms < 500, f"cancel_run took {elapsed_ms:.0f} ms"
        assert summary.status == RunStatus.CANCELLED
        assert summary.stop_reason == StopReason.CANCELLED
        assert summary.ended_at is not None
        assert supervisor.cancelled == [run.id]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_records_cancel_reason_in_final_state(
        self, db_session: AsyncSession
    ) -> None:
        run = await _seed_run(db_session, status=RunStatus.RUNNING)
        supervisor = _FakeSupervisor()

        await cancel_run(
            run.id,
            CancelRunRequest(reason="budget"),
            db_session,
            supervisor=supervisor,  # type: ignore[arg-type]
        )

        await db_session.refresh(run)
        assert run.final_state is not None
        assert run.final_state["cancel_reason"] == "budget"
        assert run.final_state["cancelled_via"] == "api"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_second_cancel_is_idempotent_no_supervisor_call(
        self, db_session: AsyncSession
    ) -> None:
        run = await _seed_run(db_session, status=RunStatus.COMPLETED)
        supervisor = _FakeSupervisor()

        summary = await cancel_run(
            run.id,
            CancelRunRequest(reason="late"),
            db_session,
            supervisor=supervisor,  # type: ignore[arg-type]
        )

        # Already terminal → returned as-is, supervisor untouched.
        assert summary.status == RunStatus.COMPLETED
        assert supervisor.cancelled == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unknown_id_raises_not_found(
        self, db_session: AsyncSession
    ) -> None:
        supervisor = _FakeSupervisor()
        with pytest.raises(NotFoundError):
            await cancel_run(
                uuid.uuid4(),
                CancelRunRequest(reason=None),
                db_session,
                supervisor=supervisor,  # type: ignore[arg-type]
            )
        assert supervisor.cancelled == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_real_supervisor_without_registered_task_is_noop(
        self, db_session: AsyncSession
    ) -> None:
        """After a zombie reconciliation reboot, the supervisor has no task
        for a DB row that's still ``running``.  ``cancel_run`` should still
        succeed (the supervisor.cancel is a no-op for unknown ids)."""
        from app.modules.ai.supervisor import RunSupervisor

        run = await _seed_run(db_session, status=RunStatus.RUNNING)
        supervisor = RunSupervisor()

        summary = await cancel_run(
            run.id,
            CancelRunRequest(reason="post-reboot"),
            db_session,
            supervisor=supervisor,
        )

        assert summary.status == RunStatus.CANCELLED
        assert summary.stop_reason == StopReason.CANCELLED
        assert not supervisor.is_registered(run.id)
