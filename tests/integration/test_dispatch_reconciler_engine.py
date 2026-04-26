"""Integration tests for the engine-aware dispatch reconciler (FEAT-010 / T-235).

Covers the three branches from the module docstring:

* ``ENGINE_DETAIL_CONFIRMED`` — engine reports the expected target
  state; dispatch is marked ``failed`` (run owner is gone) but the
  outbox row is preserved for ``reconcile-aux`` to materialise.
* ``ENGINE_DETAIL_DID_NOT_TRANSITION`` — engine reports a different
  state; dispatch is marked ``failed`` and the outbox row is preserved
  for operator investigation.
* ``ENGINE_DETAIL_UNCONFIRMED`` — no engine client (or no readable
  outbox); dispatch is marked ``failed`` and the outbox row is
  preserved.

Also verifies:

* Non-engine dispatches preserve the FEAT-009 ``orchestrator_restart``
  behavior bit-for-bit when scanned from the lifespan-style call.
* Already-terminal dispatches are skipped.
* Dispatches owned by a still-``running`` run are skipped under the
  default CLI semantics (``skip_run_alive=True``).
* ``--dry-run`` does not write.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import (
    DispatchMode,
    DispatchState,
    RunStatus,
    StepStatus,
)
from app.modules.ai.executors.reconcile import (
    ENGINE_DETAIL_CONFIRMED,
    ENGINE_DETAIL_DID_NOT_TRANSITION,
    ENGINE_DETAIL_UNCONFIRMED,
    DispatchReconcileReport,
    format_dispatch_report,
    reconcile_orphan_dispatches_engine_aware,
)
from app.modules.ai.models import Dispatch, PendingAuxWrite, Run, Step

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Stub engine client — shape matches FlowEngineLifecycleClient.get_item_state
# ---------------------------------------------------------------------------


@dataclass
class _StubEngine:
    states: dict[uuid.UUID, str | None]
    raise_on: set[uuid.UUID] | None = None

    async def get_item_state(self, item_id: uuid.UUID) -> str | None:
        if self.raise_on and item_id in self.raise_on:
            raise RuntimeError(f"simulated engine failure for {item_id}")
        return self.states.get(item_id)


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _seed_engine_dispatch(
    db: AsyncSession,
    *,
    run_status: str = RunStatus.FAILED,
    dispatch_state: str = DispatchState.DISPATCHED,
    engine_item_id: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
    to_status: str = "review",
    transition_key: str = "work_item.W2",
    seed_outbox: bool = True,
) -> tuple[Dispatch, PendingAuxWrite | None]:
    """Build a Run + Step + engine-mode Dispatch + matching outbox row."""
    item_id = engine_item_id or uuid.uuid4()
    correlation = correlation_id or uuid.uuid4()

    run = Run(
        agent_ref="test-agent@0.1.0",
        agent_definition_hash="sha256:" + "0" * 64,
        intake={},
        status=run_status,
        started_at=_now(),
        trace_uri="file:///tmp/t.jsonl",
    )
    db.add(run)
    await db.flush()
    step = Step(
        run_id=run.id,
        step_number=1,
        node_name="request_engine_transition",
        node_inputs={},
        status=StepStatus.PENDING,
    )
    db.add(step)
    await db.flush()

    dispatch = Dispatch(
        step_id=step.id,
        run_id=run.id,
        executor_ref=f"engine:{transition_key}",
        mode=DispatchMode.ENGINE,
        state=dispatch_state,
        intake={
            "engineItemId": str(item_id),
            "correlation_id": str(correlation),
            "transition_key": transition_key,
            "to_status": to_status,
        },
        dispatched_at=_now() if dispatch_state == DispatchState.DISPATCHED else None,
    )
    db.add(dispatch)

    pending: PendingAuxWrite | None = None
    if seed_outbox:
        pending = PendingAuxWrite(
            correlation_id=correlation,
            signal_name=transition_key,
            entity_type="work_item",
            entity_id=item_id,
            payload={
                "aux_type": "engine_dispatch",
                "transition_key": transition_key,
                "to_status": to_status,
            },
        )
        db.add(pending)

    await db.commit()
    return dispatch, pending


def _shim(db: AsyncSession):
    """Wrap the savepoint-bound session as a session-factory."""

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield db

    return factory


# ---------------------------------------------------------------------------
# Engine read-API present
# ---------------------------------------------------------------------------


class TestEnginePresent:
    async def test_confirmed_transition_marks_failed_confirmed(self, db_session: AsyncSession) -> None:
        item_id = uuid.uuid4()
        dispatch, pending = await _seed_engine_dispatch(
            db_session,
            engine_item_id=item_id,
            to_status="review",
        )
        engine = _StubEngine(states={item_id: "review"})

        report = await reconcile_orphan_dispatches_engine_aware(
            _shim(db_session),
            engine_client=engine,  # type: ignore[arg-type]
        )

        assert report.engine_confirmed == 1
        assert report.engine_did_not_transition == 0
        assert report.engine_unconfirmed == 0

        # Dispatch settled.
        await db_session.refresh(dispatch)
        assert dispatch.state == DispatchState.FAILED
        assert dispatch.detail == ENGINE_DETAIL_CONFIRMED

        # Outbox row preserved — reconcile-aux materialises later.
        assert pending is not None
        still = await db_session.scalar(
            select(PendingAuxWrite).where(PendingAuxWrite.correlation_id == pending.correlation_id)
        )
        assert still is not None

    async def test_did_not_transition_marks_failed_with_dnt_detail(self, db_session: AsyncSession) -> None:
        item_id = uuid.uuid4()
        dispatch, pending = await _seed_engine_dispatch(
            db_session,
            engine_item_id=item_id,
            to_status="review",
        )
        engine = _StubEngine(states={item_id: "in_progress"})  # not review

        report = await reconcile_orphan_dispatches_engine_aware(
            _shim(db_session),
            engine_client=engine,  # type: ignore[arg-type]
        )

        assert report.engine_did_not_transition == 1
        assert report.engine_confirmed == 0

        await db_session.refresh(dispatch)
        assert dispatch.state == DispatchState.FAILED
        assert dispatch.detail == ENGINE_DETAIL_DID_NOT_TRANSITION

        # Outbox preserved.
        assert pending is not None
        still = await db_session.scalar(
            select(PendingAuxWrite).where(PendingAuxWrite.correlation_id == pending.correlation_id)
        )
        assert still is not None

    async def test_engine_get_state_failure_falls_back_to_unconfirmed(self, db_session: AsyncSession) -> None:
        item_id = uuid.uuid4()
        dispatch, _ = await _seed_engine_dispatch(db_session, engine_item_id=item_id)
        engine = _StubEngine(states={item_id: "review"}, raise_on={item_id})

        report = await reconcile_orphan_dispatches_engine_aware(
            _shim(db_session),
            engine_client=engine,  # type: ignore[arg-type]
        )

        assert report.engine_unconfirmed == 1
        await db_session.refresh(dispatch)
        assert dispatch.state == DispatchState.FAILED
        assert dispatch.detail == ENGINE_DETAIL_UNCONFIRMED


# ---------------------------------------------------------------------------
# Engine read-API absent (conservative branch)
# ---------------------------------------------------------------------------


class TestEngineAbsent:
    async def test_no_engine_client_marks_unconfirmed(self, db_session: AsyncSession) -> None:
        dispatch, pending = await _seed_engine_dispatch(db_session)

        report = await reconcile_orphan_dispatches_engine_aware(
            _shim(db_session),
            engine_client=None,
        )

        assert report.engine_unconfirmed == 1
        assert report.engine_confirmed == 0

        await db_session.refresh(dispatch)
        assert dispatch.state == DispatchState.FAILED
        assert dispatch.detail == ENGINE_DETAIL_UNCONFIRMED
        # Outbox row preserved.
        assert pending is not None
        still = await db_session.scalar(
            select(PendingAuxWrite).where(PendingAuxWrite.correlation_id == pending.correlation_id)
        )
        assert still is not None

    async def test_no_outbox_row_falls_back_to_unconfirmed(self, db_session: AsyncSession) -> None:
        """Engine present but the outbox row is missing — conservative."""
        item_id = uuid.uuid4()
        dispatch, _ = await _seed_engine_dispatch(db_session, engine_item_id=item_id, seed_outbox=False)
        engine = _StubEngine(states={item_id: "review"})

        report = await reconcile_orphan_dispatches_engine_aware(
            _shim(db_session),
            engine_client=engine,  # type: ignore[arg-type]
        )

        assert report.engine_unconfirmed == 1
        await db_session.refresh(dispatch)
        assert dispatch.detail == ENGINE_DETAIL_UNCONFIRMED


# ---------------------------------------------------------------------------
# Skip semantics
# ---------------------------------------------------------------------------


class TestSkipSemantics:
    async def test_running_run_is_skipped_under_cli_semantics(self, db_session: AsyncSession) -> None:
        item_id = uuid.uuid4()
        dispatch, _ = await _seed_engine_dispatch(
            db_session,
            engine_item_id=item_id,
            run_status=RunStatus.RUNNING,
        )
        engine = _StubEngine(states={item_id: "review"})

        report = await reconcile_orphan_dispatches_engine_aware(
            _shim(db_session),
            engine_client=engine,  # type: ignore[arg-type]
            skip_run_alive=True,
        )

        assert report.skipped_run_alive == 1
        assert report.engine_confirmed == 0

        await db_session.refresh(dispatch)
        assert dispatch.state == DispatchState.DISPATCHED  # untouched

    async def test_already_terminal_dispatch_is_skipped(self, db_session: AsyncSession) -> None:
        # A dispatch already in COMPLETED state must not be re-touched.
        # We seed it via the DB directly because mark_completed enforces
        # legal transitions and we want the row at terminal up front.
        item_id = uuid.uuid4()
        dispatch, _ = await _seed_engine_dispatch(
            db_session,
            engine_item_id=item_id,
            dispatch_state=DispatchState.DISPATCHED,
        )
        # Pre-terminate it.
        dispatch.mark_completed(at=_now(), result={"ok": True})
        await db_session.commit()

        engine = _StubEngine(states={item_id: "review"})
        report = await reconcile_orphan_dispatches_engine_aware(
            _shim(db_session),
            engine_client=engine,  # type: ignore[arg-type]
        )

        # The terminal row shouldn't even be picked up by the query
        # (filter on PENDING/DISPATCHED), so scanned is 0.
        assert report.scanned == 0
        assert report.engine_confirmed == 0
        assert report.engine_unconfirmed == 0


# ---------------------------------------------------------------------------
# Non-engine modes preserve FEAT-009 conservative-cancel
# ---------------------------------------------------------------------------


class TestNonEngineMode:
    async def test_remote_dispatch_cancelled_with_orchestrator_restart_detail(self, db_session: AsyncSession) -> None:
        run = Run(
            agent_ref="lifecycle-agent@0.2.0",
            agent_definition_hash="sha256:" + "0" * 64,
            intake={},
            status=RunStatus.FAILED,
            started_at=_now(),
            trace_uri="file:///tmp/t.jsonl",
        )
        db_session.add(run)
        await db_session.flush()
        step = Step(
            run_id=run.id,
            step_number=1,
            node_name="x",
            node_inputs={},
            status=StepStatus.PENDING,
        )
        db_session.add(step)
        await db_session.flush()
        dispatch = Dispatch(
            step_id=step.id,
            run_id=run.id,
            executor_ref="remote:claude-code",
            mode=DispatchMode.REMOTE,
            state=DispatchState.DISPATCHED,
            intake={"task_id": "T-001"},
            dispatched_at=_now(),
        )
        db_session.add(dispatch)
        await db_session.commit()

        report = await reconcile_orphan_dispatches_engine_aware(
            _shim(db_session),
            engine_client=None,
        )

        assert report.cancelled_non_engine == 1
        await db_session.refresh(dispatch)
        assert dispatch.state == DispatchState.CANCELLED
        assert dispatch.detail == "orchestrator_restart"


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


class TestDryRun:
    async def test_dry_run_does_not_write(self, db_session: AsyncSession) -> None:
        item_id = uuid.uuid4()
        dispatch, _ = await _seed_engine_dispatch(db_session, engine_item_id=item_id)
        engine = _StubEngine(states={item_id: "review"})

        report = await reconcile_orphan_dispatches_engine_aware(
            _shim(db_session),
            engine_client=engine,  # type: ignore[arg-type]
            dry_run=True,
        )

        # Report counts what *would* happen.
        assert report.engine_confirmed == 1
        # ... but the dispatch row stays DISPATCHED.
        await db_session.refresh(dispatch)
        assert dispatch.state == DispatchState.DISPATCHED


# ---------------------------------------------------------------------------
# format_dispatch_report
# ---------------------------------------------------------------------------


def test_format_dispatch_report_shape() -> None:
    report = DispatchReconcileReport(
        scanned=3,
        cancelled_non_engine=1,
        engine_confirmed=1,
        engine_did_not_transition=0,
        engine_unconfirmed=1,
        skipped_run_alive=0,
        skipped_already_terminal=0,
        errors=["abc: boom"],
    )
    out = format_dispatch_report(report, dry_run=True)
    assert "dry-run: true" in out
    assert "Scanned:" in out
    assert "Engine confirmed:" in out
    assert "Engine unconfirmed:" in out
    assert "! abc: boom" in out
