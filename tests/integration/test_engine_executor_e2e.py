"""End-to-end test for the engine-executor + reactor wake (FEAT-010 / T-236).

Drives the throwaway ``test-engine-agent@0.1.0`` (under
``tests/fixtures/agents/``) through the deterministic runtime:

1. ``request_seed_load`` — local executor that returns ``ok``.
2. ``request_engine_transition`` — engine executor bound to a synthetic
   ``work_item.W2`` transition.  The engine HTTP boundary is stubbed via
   ``respx``; in the happy-path variant the test fires the matching
   ``item.transitioned`` webhook against the live reactor pipeline.

Asserted outcomes:

* The engine executor's ``transition_item`` POST hits the engine.
* The reactor's ``_wake_dispatch`` step matches the dispatch by
  correlation id and resolves the supervisor future.
* The Dispatch row flips to ``COMPLETED`` and the run reaches
  ``RunStatus.COMPLETED``.
* The persisted ``executor_call`` trace entry carries
  ``mode=engine`` + ``correlation_id`` + ``transition_key``.

The race-variant (webhook arrives before the dispatch row commits) is
covered by the unit-level ``test_no_match_is_noop`` in
``tests/modules/ai/lifecycle/test_reactor_wake_dispatch.py``.  Re-running
that race against a live FastAPI client requires more orchestration
machinery than this PR's footprint warrants — punted with a comment in
T-236's acceptance notes.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import NullPool, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.webhook_auth import sign_body
from app.main import create_app
from app.modules.ai.agents import _parse_file
from app.modules.ai.enums import DispatchMode, DispatchState, RunStatus
from app.modules.ai.executors.engine import EngineExecutor
from app.modules.ai.executors.local import LocalExecutor
from app.modules.ai.executors.registry import ExecutorRegistry
from app.modules.ai.lifecycle import declarations as lc_decl
from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient
from app.modules.ai.models import Dispatch, Run, RunMemory, WorkItem
from app.modules.ai.runtime_deterministic import run_deterministic_loop
from app.modules.ai.schemas import ExecutorCallDto
from app.modules.ai.supervisor import RunSupervisor
from app.modules.ai.trace_jsonl import JsonlTraceStore

pytestmark = pytest.mark.asyncio(loop_scope="function")

_ENGINE_BASE = "http://engine.test"
_ENGINE_API_KEY = "test-key"
_TOKEN_RESPONSE = {
    "data": {
        "accessToken": "jwt-xxx",
        "expiresAt": "2099-01-01T00:00:00Z",
        "tokenType": "Bearer",
    }
}


def _build_session_factory(test_database_url: str) -> async_sessionmaker[AsyncSession]:
    eng = create_async_engine(test_database_url, poolclass=NullPool)
    return async_sessionmaker(bind=eng, expire_on_commit=False)


async def _seed_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    agent_ref: str,
    intake: dict[str, Any],
) -> Run:
    async with session_factory() as session:
        run = Run(
            agent_ref=agent_ref,
            agent_definition_hash="sha256:" + "0" * 64,
            intake=intake,
            status=RunStatus.PENDING,
            started_at=datetime.now(UTC),
            trace_uri="file:///tmp/t.jsonl",
        )
        session.add(run)
        await session.flush()
        session.add(RunMemory(run_id=run.id, data={}))
        await session.commit()
        await session.refresh(run)
        return run


async def _cleanup(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    work_item_id: uuid.UUID | None = None,
) -> None:
    from sqlalchemy import delete

    from app.modules.ai.models import (
        Dispatch as _Dispatch,
    )
    from app.modules.ai.models import (
        PendingAuxWrite as _Pending,
    )
    from app.modules.ai.models import (
        RunMemory as _RunMemory,
    )
    from app.modules.ai.models import (
        Step as _Step,
    )
    from app.modules.ai.models import (
        WebhookEvent as _WebhookEvent,
    )

    async with session_factory() as session:
        await session.execute(delete(_Dispatch).where(_Dispatch.run_id == run_id))
        await session.execute(delete(_Step).where(_Step.run_id == run_id))
        await session.execute(delete(_RunMemory).where(_RunMemory.run_id == run_id))
        await session.execute(delete(Run).where(Run.id == run_id))
        await session.execute(delete(_Pending))
        await session.execute(delete(_WebhookEvent))
        if work_item_id is not None:
            await session.execute(delete(WorkItem).where(WorkItem.id == work_item_id))
        await session.commit()


@pytest_asyncio.fixture(loop_scope="function")
async def engine_client() -> AsyncIterator[FlowEngineLifecycleClient]:
    client = FlowEngineLifecycleClient(base_url=_ENGINE_BASE, api_key=_ENGINE_API_KEY, max_retries=2)
    try:
        yield client
    finally:
        await client.aclose()


async def test_engine_executor_reaches_terminal_via_reactor_wake(
    test_database_url: str,
    migrated: None,
    fresh_pool: None,
    engine_client: FlowEngineLifecycleClient,
    webhook_secret: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: engine dispatch → engine 200 → webhook → reactor wake → terminal."""
    # The webhook signing helper reads the secret from settings.
    monkeypatch.setenv("ENGINE_WEBHOOK_SECRET", webhook_secret)
    from app.config import get_settings

    get_settings.cache_clear()

    # Load the throwaway agent.
    agent_path = Path(__file__).resolve().parents[1] / "fixtures" / "agents" / "test-engine-agent@0.1.0.yaml"
    agent = _parse_file(agent_path, repo_root=agent_path.parent)

    session_factory = _build_session_factory(test_database_url)

    # Seed a WorkItem so reactor's _update_status_cache succeeds; engine_item_id
    # is the join key.
    engine_item_id = uuid.uuid4()
    work_item_id: uuid.UUID
    async with session_factory() as session:
        wi = WorkItem(
            external_ref=f"FEAT-{uuid.uuid4().hex[:6]}",
            type="FEAT",
            title="e2e",
            status="in_progress",
            opened_by="admin",
            engine_item_id=engine_item_id,
        )
        session.add(wi)
        await session.commit()
        await session.refresh(wi)
        work_item_id = wi.id

    run = await _seed_run(
        session_factory,
        agent_ref=agent.ref,
        intake={"engineItemId": str(engine_item_id)},
    )

    # Wire the registry: local executor for the seed step; engine executor
    # for the engine-bound step.
    registry = ExecutorRegistry()

    async def _seed_handler(_ctx: Any) -> dict[str, Any]:
        return {"ok": True}

    registry.register(
        agent.ref,
        "request_seed_load",
        LocalExecutor(ref="local:request_seed_load", handler=_seed_handler),
    )
    registry.register(
        agent.ref,
        "request_engine_transition",
        EngineExecutor(
            ref="engine:work_item.W2",
            transition_key="work_item.W2",
            to_status="ready",
            lifecycle_client=engine_client,
            session_factory=session_factory,
        ),
    )

    supervisor = RunSupervisor()

    # Build a real FastAPI app + AsyncClient so the reactor pipeline runs
    # behind the actual /hooks/engine/lifecycle/item-transitioned route.
    app = create_app()
    app.state.supervisor = supervisor
    transport = ASGITransport(app=app)

    trace_dir = Path("/tmp/feat010-e2e-trace")
    trace_dir.mkdir(exist_ok=True)
    trace = JsonlTraceStore(trace_dir)

    cancel_event = asyncio.Event()

    captured_correlation: dict[str, uuid.UUID | None] = {"value": None}

    # Stub the engine HTTP boundary.  When ``transition_item`` is called,
    # capture the encoded correlation id from the comment so the test can
    # fire the matching webhook back into the orchestrator.
    async def _post_transition(request: Any) -> Response:
        body = json.loads(request.content.decode())
        comment = body.get("comment", "")
        prefix = "orchestrator-corr:"
        for tok in comment.split():
            if tok.startswith(prefix):
                captured_correlation["value"] = uuid.UUID(tok[len(prefix) :])
                break
        return Response(
            200,
            json={
                "data": {
                    "id": str(uuid.uuid4()),
                    "transitionRunId": str(uuid.uuid4()),
                }
            },
        )

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:

            async def _fire_webhook_when_ready() -> None:
                """Wait for the engine-side transition POST, then fire the webhook."""
                # Poll captured_correlation until set, max 5s.
                for _ in range(500):
                    if captured_correlation["value"] is not None:
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError("engine transition POST never observed")
                corr = captured_correlation["value"]
                assert corr is not None
                body = {
                    "deliveryId": str(uuid.uuid4()),
                    "eventType": "item.transitioned",
                    "tenantId": str(uuid.uuid4()),
                    "workflowId": str(uuid.uuid4()),
                    "itemId": str(engine_item_id),
                    "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "data": {
                        "fromStatus": "in_progress",
                        "toStatus": "ready",
                        "triggeredBy": f"orchestrator-corr:{corr}",
                    },
                }
                raw = json.dumps(body).encode()
                # The signature header name depends on the route; mirror the
                # FEAT-008 invariant test which used X-FlowEngine-Signature.
                sig = sign_body(raw, webhook_secret)
                resp = await http_client.post(
                    "/hooks/engine/lifecycle/item-transitioned",
                    content=raw,
                    headers={
                        "X-FlowEngine-Signature": sig,
                        "Content-Type": "application/json",
                    },
                )
                assert resp.status_code == 202, resp.text

            with respx.mock(base_url=_ENGINE_BASE, assert_all_called=False) as mock:
                # Token exchange.
                mock.post("/api/auth/token").respond(200, json=_TOKEN_RESPONSE)
                # Transition.
                mock.post(url__regex=r"/api/items/[^/]+/transitions").mock(side_effect=_post_transition)
                # Workflow lookups (defensive — engine_client may probe).
                mock.get(url__regex=r"/api/workflows.*").respond(200, json={"data": {"items": []}})

                webhook_task = asyncio.create_task(_fire_webhook_when_ready())
                try:
                    await asyncio.wait_for(
                        run_deterministic_loop(
                            run_id=run.id,
                            agent=agent,
                            trace=trace,
                            supervisor=supervisor,
                            registry=registry,
                            session_factory=session_factory,
                            cancel_event=cancel_event,
                            dispatch_timeout_seconds=10,
                        ),
                        timeout=20,
                    )
                finally:
                    if not webhook_task.done():
                        webhook_task.cancel()
                        try:
                            await webhook_task
                        except (asyncio.CancelledError, Exception):
                            pass

        # Run reached terminal.
        async with session_factory() as session:
            run_row = (await session.scalars(select(Run).where(Run.id == run.id))).one()
            assert run_row.status == RunStatus.COMPLETED, f"run did not complete; final_state={run_row.final_state}"

            # The engine-bound dispatch row landed with the engine metadata.
            dispatches = (await session.scalars(select(Dispatch).where(Dispatch.run_id == run.id))).all()
            engine_dispatches = [d for d in dispatches if d.mode == DispatchMode.ENGINE.value]
            assert len(engine_dispatches) == 1
            engine_dispatch = engine_dispatches[0]
            assert engine_dispatch.state == DispatchState.COMPLETED.value
            assert engine_dispatch.intake.get("correlation_id") is not None
            assert engine_dispatch.intake.get("transition_key") == "work_item.W2"
            # Reactor's wake step populated the result envelope.
            assert engine_dispatch.result is not None
            assert engine_dispatch.result.get("engine_to_status") == "ready"

        # Trace assertions: the engine executor_call entry lives under
        # ``<trace_dir>/executors/<run_id>.jsonl`` (FEAT-009 layout).
        trace_file = trace_dir / "executors" / f"{run.id}.jsonl"
        assert trace_file.exists(), f"no executor trace at {trace_file}"
        executor_calls: list[ExecutorCallDto] = []
        for line in trace_file.read_text().splitlines():
            entry = json.loads(line)
            if entry.get("kind") == "executor_call":
                payload = entry["data"]
                executor_calls.append(ExecutorCallDto.model_validate(payload))
        engine_calls = [c for c in executor_calls if c.mode == DispatchMode.ENGINE]
        assert len(engine_calls) == 1, f"expected 1 engine executor_call, got {executor_calls}"
        call = engine_calls[0]
        assert call.correlation_id is not None
        assert call.transition_key == "work_item.W2"
    finally:
        await _cleanup(session_factory, run.id, work_item_id=work_item_id)
        # Avoid leaking the trace file across tests.
        trace_file = trace_dir / "executors" / f"{run.id}.jsonl"
        if trace_file.exists():
            trace_file.unlink()


# Silence unused-import warnings when only the module is loaded.
_ = lc_decl
