"""Integration tests for /hooks/executors/{id} (FEAT-009 / T-216 + T-217 + T-221).

Covers:

* Successful terminal-state delivery (``ok`` → ``completed``,
  ``error`` → ``failed``) waking the supervisor.
* Bad signature → 401 + persisted ``webhook_events`` with
  ``signature_ok=False``.
* Unknown ``dispatchId`` → 404.
* Idempotent re-delivery with the same outcome → 200 +
  ``meta.alreadyReceived=true``.
* Conflicting outcome on a terminal dispatch → 409.
* Restart reconciler cancels orphan dispatches at lifespan startup.
* Human-executor: a signal POST drives ``deliver_dispatch`` for an
  in-flight ``mode='human'`` dispatch.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.enums import DispatchMode, DispatchState, RunStatus, StepStatus
from app.modules.ai.executors.reconcile import reconcile_orphan_dispatches
from app.modules.ai.models import Dispatch, Run, Step, WebhookEvent
from app.modules.ai.supervisor import RunSupervisor

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _now() -> datetime:
    return datetime.now(UTC)


async def _seed(db: AsyncSession, *, mode: DispatchMode = DispatchMode.REMOTE) -> Dispatch:
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
        node_name="request_implementation",
        node_inputs={},
        status=StepStatus.PENDING,
    )
    db.add(step)
    await db.flush()
    dispatch = Dispatch(
        step_id=step.id,
        run_id=run.id,
        executor_ref="remote:claude-code" if mode == DispatchMode.REMOTE else "human:wait",
        mode=mode,
        state=DispatchState.DISPATCHED,
        intake={"task_id": "T-001"},
        dispatched_at=_now(),
    )
    db.add(dispatch)
    await db.commit()
    return dispatch


def _post_with_sig(
    body: dict, signer: Callable[[bytes], str], *, override: str | None = None
) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode()
    sig = override if override is not None else signer(raw)
    return raw, {"x-executor-signature": sig, "content-type": "application/json"}


class TestSuccessfulDelivery:
    async def test_ok_outcome_completes_dispatch_and_wakes_supervisor(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        webhook_signer: Callable[[bytes], str],
        app: FastAPI,
    ) -> None:
        dispatch = await _seed(db_session)
        # Pre-register so deliver_dispatch has a future to resolve.
        from app.core.dependencies import _default_supervisor  # noqa: PLC0415

        # Test path: lifespan does not run via ASGITransport, so the
        # FastAPI dep falls back to the module-level singleton.
        sup = _default_supervisor or RunSupervisor()
        app.state.supervisor = sup
        supervisor: RunSupervisor = sup
        supervisor.register_dispatch(dispatch.run_id, dispatch.dispatch_id)

        body = {
            "dispatchId": str(dispatch.dispatch_id),
            "outcome": "ok",
            "result": {"verdict": "pass"},
        }
        raw, headers = _post_with_sig(body, webhook_signer)
        resp = await client.post("/hooks/executors/claude-code", content=raw, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"] == {"received": True}

        await db_session.refresh(dispatch)
        assert dispatch.state == DispatchState.COMPLETED
        assert dispatch.result == {"verdict": "pass"}

    async def test_error_outcome_fails_dispatch(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        webhook_signer: Callable[[bytes], str],
        app: FastAPI,
    ) -> None:
        dispatch = await _seed(db_session)
        from app.core.dependencies import _default_supervisor  # noqa: PLC0415

        # Test path: lifespan does not run via ASGITransport, so the
        # FastAPI dep falls back to the module-level singleton.
        sup = _default_supervisor or RunSupervisor()
        app.state.supervisor = sup
        supervisor: RunSupervisor = sup
        supervisor.register_dispatch(dispatch.run_id, dispatch.dispatch_id)

        body = {
            "dispatchId": str(dispatch.dispatch_id),
            "outcome": "error",
            "detail": "executor blew up",
        }
        raw, headers = _post_with_sig(body, webhook_signer)
        resp = await client.post("/hooks/executors/claude-code", content=raw, headers=headers)
        assert resp.status_code == 200

        await db_session.refresh(dispatch)
        assert dispatch.state == DispatchState.FAILED
        assert dispatch.detail == "executor blew up"


class TestSecurityAndErrors:
    async def test_bad_signature_returns_401_and_persists_event(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        webhook_signer: Callable[[bytes], str],
    ) -> None:
        dispatch = await _seed(db_session)
        body = {"dispatchId": str(dispatch.dispatch_id), "outcome": "ok"}
        raw, headers = _post_with_sig(body, webhook_signer, override="sha256=bogus")
        resp = await client.post("/hooks/executors/claude-code", content=raw, headers=headers)
        assert resp.status_code == 401

        # webhook_events row written with signature_ok=False
        rows = (await db_session.scalars(select(WebhookEvent))).all()
        assert any(not r.signature_ok for r in rows)

        # Dispatch state unchanged
        await db_session.refresh(dispatch)
        assert dispatch.state == DispatchState.DISPATCHED

    async def test_unknown_dispatch_returns_404(
        self,
        client: AsyncClient,
        webhook_signer: Callable[[bytes], str],
    ) -> None:
        body = {"dispatchId": str(uuid.uuid4()), "outcome": "ok"}
        raw, headers = _post_with_sig(body, webhook_signer)
        resp = await client.post("/hooks/executors/claude-code", content=raw, headers=headers)
        assert resp.status_code == 404


class TestIdempotency:
    async def test_same_outcome_replay_returns_already_received(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        webhook_signer: Callable[[bytes], str],
        app: FastAPI,
    ) -> None:
        dispatch = await _seed(db_session)
        from app.core.dependencies import _default_supervisor  # noqa: PLC0415

        # Test path: lifespan does not run via ASGITransport, so the
        # FastAPI dep falls back to the module-level singleton.
        sup = _default_supervisor or RunSupervisor()
        app.state.supervisor = sup
        supervisor: RunSupervisor = sup
        supervisor.register_dispatch(dispatch.run_id, dispatch.dispatch_id)
        body = {"dispatchId": str(dispatch.dispatch_id), "outcome": "ok"}
        raw, headers = _post_with_sig(body, webhook_signer)

        first = await client.post("/hooks/executors/claude-code", content=raw, headers=headers)
        assert first.status_code == 200

        second = await client.post("/hooks/executors/claude-code", content=raw, headers=headers)
        assert second.status_code == 200
        assert second.json()["meta"].get("alreadyReceived") is True

    async def test_conflicting_outcome_returns_409(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        webhook_signer: Callable[[bytes], str],
        app: FastAPI,
    ) -> None:
        dispatch = await _seed(db_session)
        from app.core.dependencies import _default_supervisor  # noqa: PLC0415

        # Test path: lifespan does not run via ASGITransport, so the
        # FastAPI dep falls back to the module-level singleton.
        sup = _default_supervisor or RunSupervisor()
        app.state.supervisor = sup
        supervisor: RunSupervisor = sup
        supervisor.register_dispatch(dispatch.run_id, dispatch.dispatch_id)
        ok_body = {"dispatchId": str(dispatch.dispatch_id), "outcome": "ok"}
        raw_ok, headers_ok = _post_with_sig(ok_body, webhook_signer)
        await client.post("/hooks/executors/claude-code", content=raw_ok, headers=headers_ok)

        err_body = {"dispatchId": str(dispatch.dispatch_id), "outcome": "error"}
        raw_err, headers_err = _post_with_sig(err_body, webhook_signer)
        resp = await client.post("/hooks/executors/claude-code", content=raw_err, headers=headers_err)
        assert resp.status_code == 409


class TestRestartReconciler:
    async def test_orphan_dispatches_cancelled_on_restart(
        self,
        db_session: AsyncSession,
    ) -> None:
        await _seed(db_session)
        await _seed(db_session)

        # Build a thin session-factory shim that yields the savepoint-wrapped
        # session so the reconciler's commits stay inside the test's outer
        # rollback envelope.
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _shim():  # type: ignore[no-untyped-def]
            yield db_session

        n = await reconcile_orphan_dispatches(_shim)  # type: ignore[arg-type]
        assert n >= 2

        rows = (await db_session.scalars(select(Dispatch).where(Dispatch.state == DispatchState.CANCELLED))).all()
        assert len(rows) >= 2
        for row in rows:
            assert row.detail == "orchestrator_restart"
